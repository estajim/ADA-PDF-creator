"""Stage 3 — OCR scanned/mixed pages using Surya (primary) or Tesseract (fallback)."""
from __future__ import annotations

import io
from typing import Any

from ada_pdf.models.domain import PageType  # noqa: F401 — used in page_type check
from ada_pdf.pipeline.context import PipelineContext
from ada_pdf.utils.logging import get_logger

logger = get_logger(__name__)


class OCRError(Exception):
    pass


def run(ctx: PipelineContext, settings) -> PipelineContext:
    logger.info("stage_start", stage="ocr")

    # If user forced doc_type=digital, skip OCR entirely
    if getattr(ctx, "force_doc_type", None) == "digital":
        logger.info("stage_done", stage="ocr", note="skipped_forced_digital")
        return ctx

    pages_to_ocr = [
        p for p in ctx.raw_pages
        if p.__dict__.get("page_type") in (PageType.SCANNED, PageType.MIXED)
        and p.image_bytes is not None
    ]

    if not pages_to_ocr:
        logger.info("stage_done", stage="ocr", note="no_scanned_pages")
        return ctx

    provider = settings.OCR_PRIMARY.value
    tess_cmd = getattr(settings, "TESSERACT_CMD", None)

    for raw_page in pages_to_ocr:
        page_type = raw_page.__dict__.get("page_type", PageType.SCANNED)
        has_native_text = bool(raw_page.raw_fitz_dict)   # MIXED pages have this

        try:
            if provider == "surya":
                ocr_blocks = _run_surya(raw_page.image_bytes)
            else:
                ocr_blocks = _run_tesseract(raw_page.image_bytes, tess_cmd)
        except Exception as primary_exc:
            logger.warning("ocr_primary_failed", page=raw_page.page_number,
                           error=str(primary_exc)[:120])
            try:
                ocr_blocks = _run_tesseract(raw_page.image_bytes, tess_cmd)
            except Exception as fallback_exc:
                # OCR failure is never fatal — pages with native text lose nothing;
                # purely scanned pages will have no selectable text but still produce a PDF.
                logger.warning(
                    "ocr_failed_continuing",
                    page=raw_page.page_number,
                    has_native_text=has_native_text,
                    error=str(fallback_exc)[:120],
                )
                raw_page.ocr_blocks = []
                continue

        raw_page.ocr_blocks = ocr_blocks
        logger.debug("ocr_done", page=raw_page.page_number, blocks=len(ocr_blocks))

    logger.info("stage_done", stage="ocr", pages_processed=len(pages_to_ocr))
    return ctx


def _run_surya(image_bytes: bytes) -> list[dict]:
    from PIL import Image
    from surya.recognition import run_recognition  # type: ignore[import]
    from surya.model.recognition.model import load_model  # type: ignore[import]
    from surya.model.recognition.processor import load_processor  # type: ignore[import]

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    model = load_model()
    processor = load_processor()

    results = run_recognition([image], [["en"]], model, processor)
    blocks = []
    for result in results:
        for line in result.text_lines:
            if line.text.strip():
                bbox = line.bbox
                blocks.append({
                    "text": line.text,
                    "x0": bbox[0], "y0": bbox[1],
                    "x1": bbox[2], "y1": bbox[3],
                    "confidence": line.confidence,
                })
    return blocks


def _run_tesseract(image_bytes: bytes, cmd: str | None = None) -> list[dict]:
    import pytesseract
    from PIL import Image

    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

    blocks = []
    for i, text in enumerate(data["text"]):
        text = text.strip()
        if not text or float(data["conf"][i]) < 30:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        blocks.append({
            "text": text,
            "x0": float(x), "y0": float(y),
            "x1": float(x + w), "y1": float(y + h),
            "confidence": float(data["conf"][i]),
        })
    return blocks
