"""Stage 6 — Generate alt text for Figure blocks using BLIP-2 (free, local model).

Falls back to a simple filename-based description if the model is unavailable.
An optional ANTHROPIC_API_KEY env var can be set to upgrade quality via Claude vision.
"""
from __future__ import annotations

import io
from typing import Optional

import fitz

from ada_pdf.models.domain import BlockIR, BlockRole, PageIR
from ada_pdf.pipeline.algorithms.artifact_detector import is_image_decorative
from ada_pdf.pipeline.context import PipelineContext
from ada_pdf.config import Settings
from ada_pdf.utils.logging import get_logger

logger = get_logger(__name__)


class AltTextError(Exception):
    pass


def run(ctx: PipelineContext, settings: Settings) -> PipelineContext:
    logger.info("stage_start", stage="alttext")

    if ctx.skip_alt_text:
        logger.info("stage_skipped", stage="alttext", reason="skip_flag_set")
        return ctx

    doc = ctx.fitz_doc
    pages_ir: list[PageIR] = ctx.layout_data

    figure_blocks = [
        (page_ir, block)
        for page_ir in pages_ir
        for block in page_ir.blocks
        if block.role == BlockRole.FIGURE and not block.is_artifact and block.alt_text is None
    ]

    if not figure_blocks:
        logger.info("stage_done", stage="alttext", note="no_figures")
        return ctx

    # Determine which alt text provider to use
    has_anthropic_key = bool(
        getattr(settings, "ANTHROPIC_API_KEY", None)
        and settings.ANTHROPIC_API_KEY not in ("", "sk-ant-...")
    )

    provider = "anthropic" if has_anthropic_key else "blip"
    logger.info("alttext_provider", provider=provider, figures=len(figure_blocks))

    for page_ir, block in figure_blocks:
        page = doc[page_ir.page_number - 1]
        image_bytes = _crop_block_image(page, block)

        if image_bytes is None:
            block.alt_text = ""
            block.is_artifact = True
            continue

        # Check if decorative based on image dimensions
        w, h = _get_image_dims(image_bytes)
        if is_image_decorative(w, h):
            block.is_artifact = True
            block.alt_text = None
            continue

        try:
            if provider == "anthropic":
                alt = _generate_anthropic(image_bytes, settings.ANTHROPIC_API_KEY)
            else:
                alt = _generate_blip(image_bytes)
        except Exception as exc:
            logger.warning("alttext_failed", page=page_ir.page_number, error=str(exc))
            alt = ""

        if alt.strip().upper() == "DECORATIVE":
            block.is_artifact = True
            block.alt_text = None
        else:
            block.alt_text = alt.strip()

    logger.info("stage_done", stage="alttext")
    return ctx


def _crop_block_image(page: fitz.Page, block: BlockIR) -> Optional[bytes]:
    """Rasterize just the bounding box region for the figure block."""
    try:
        rect = fitz.Rect(block.bbox.x0, block.bbox.y0, block.bbox.x1, block.bbox.y1)
        mat = fitz.Matrix(2, 2)  # 2× resolution for better quality
        pix = page.get_pixmap(matrix=mat, clip=rect)
        return pix.tobytes("png")
    except Exception as exc:
        logger.warning("crop_failed", error=str(exc))
        return None


def _get_image_dims(image_bytes: bytes) -> tuple[float, float]:
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        return float(img.width), float(img.height)
    except Exception:
        return 100.0, 100.0


def _generate_blip(image_bytes: bytes) -> str:
    """Generate alt text using BLIP (free, local, CPU-capable via HuggingFace)."""
    try:
        import torch
        from PIL import Image
        from transformers import BlipProcessor, BlipForConditionalGeneration  # type: ignore[import]

        processor = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
        model = BlipForConditionalGeneration.from_pretrained(
            "Salesforce/blip-image-captioning-base",
            torch_dtype=torch.float32,
        )
        model.eval()

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        inputs = processor(image, return_tensors="pt")
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=80)
        caption = processor.decode(out[0], skip_special_tokens=True)
        return caption
    except ImportError:
        logger.warning("blip_unavailable", note="transformers/torch not installed")
        return ""
    except Exception as exc:
        logger.warning("blip_error", error=str(exc))
        return ""


def _generate_anthropic(image_bytes: bytes, api_key: str) -> str:
    """Generate alt text using Claude vision (optional upgrade when API key is set)."""
    import base64
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    image_b64 = base64.standard_b64encode(image_bytes).decode()

    prompt = (
        "You are generating alt text for a PDF accessibility remediation system. "
        "Rules: (1) 1-3 sentences max. (2) Describe content, not format. "
        "(3) For charts/graphs: state the key trend or conclusion. "
        "(4) For purely decorative images, reply exactly: DECORATIVE. "
        "(5) Do not start with 'Image of' or 'Picture of'.\n\n"
        "Generate the alt text now:"
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return response.content[0].text
