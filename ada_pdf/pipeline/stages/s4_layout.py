"""Stage 4 — Layout analysis via Docling + artifact detection."""
from __future__ import annotations

import io
from pathlib import Path

from ada_pdf.models.domain import BBox, BlockIR, BlockRole, PageIR, PageType, SpanIR
from ada_pdf.pipeline.algorithms.artifact_detector import classify_block
from ada_pdf.pipeline.context import PipelineContext
from ada_pdf.utils.logging import get_logger

logger = get_logger(__name__)

# Docling label → BlockRole mapping
_DOCLING_LABEL_MAP: dict[str, BlockRole] = {
    "title": BlockRole.H1,
    "section_header": BlockRole.H2,
    "text": BlockRole.P,
    "list_item": BlockRole.LI,
    "table": BlockRole.TABLE,
    "figure": BlockRole.FIGURE,
    "caption": BlockRole.CAPTION,
    "page_header": BlockRole.ARTIFACT,
    "page_footer": BlockRole.ARTIFACT,
    "footnote": BlockRole.ARTIFACT,
    "formula": BlockRole.P,
    "code": BlockRole.P,
}


def run(ctx: PipelineContext, settings) -> PipelineContext:
    logger.info("stage_start", stage="layout")

    pages_ir: list[PageIR] = []

    try:
        from docling.document_converter import DocumentConverter  # type: ignore[import]
        from docling.datamodel.base_models import InputFormat  # type: ignore[import]

        converter = DocumentConverter()
        doc_result = converter.convert(str(ctx.input_path))
        pages_ir = _from_docling(doc_result, ctx)
    except ImportError:
        logger.warning("docling_unavailable", note="falling back to fitz-only layout")
        pages_ir = _from_fitz_only(ctx)
    except Exception as exc:
        logger.warning("docling_failed", error=str(exc), note="falling back to fitz-only layout")
        pages_ir = _from_fitz_only(ctx)

    ctx.layout_data = pages_ir
    logger.info("stage_done", stage="layout", pages=len(pages_ir))
    return ctx


def _from_docling(doc_result, ctx: PipelineContext) -> list[PageIR]:
    """Convert Docling output to list[PageIR]."""
    pages_ir: list[PageIR] = []
    doc = doc_result.document

    # Group elements by page
    by_page: dict[int, list] = {}
    for element, _level in doc.iterate_items():
        prov = getattr(element, "prov", None)
        if prov is None:
            continue
        for p in prov:
            page_no = p.page_no
            by_page.setdefault(page_no, []).append((element, p.bbox))

    for raw_page in ctx.raw_pages:
        pno = raw_page.page_number
        page_type = raw_page.__dict__.get("page_type", PageType.DIGITAL)
        blocks: list[BlockIR] = []

        for element, bbox in by_page.get(pno, []):
            role = _map_docling_label(element)
            if bbox is None:
                continue

            block_bbox = BBox(
                x0=bbox.l, y0=bbox.t,
                x1=bbox.r, y1=bbox.b,
                page_number=pno,
            )

            text = getattr(element, "text", "") or ""
            span = SpanIR(text=text) if text else None

            block = BlockIR(
                role=role,
                bbox=block_bbox,
                spans=[span] if span else [],
            )

            # Apply artifact detection heuristics on top of Docling labels
            if role == BlockRole.ARTIFACT:
                block.is_artifact = True
            else:
                decision = classify_block(block_bbox, raw_page.height, raw_page.width)
                if decision.is_artifact:
                    block.is_artifact = True
                    block.role = BlockRole.ARTIFACT

            blocks.append(block)

        pages_ir.append(PageIR(
            page_number=pno,
            width=raw_page.width,
            height=raw_page.height,
            page_type=page_type,
            blocks=blocks,
        ))

    return pages_ir


def _from_fitz_only(ctx: PipelineContext) -> list[PageIR]:
    """Build layout from PyMuPDF dict output when Docling is unavailable."""
    pages_ir: list[PageIR] = []

    for raw_page in ctx.raw_pages:
        page_type = raw_page.__dict__.get("page_type", PageType.DIGITAL)
        blocks: list[BlockIR] = []
        pno = raw_page.page_number

        fitz_dict = raw_page.raw_fitz_dict
        if fitz_dict:
            for fitz_block in fitz_dict.get("blocks", []):
                if fitz_block.get("type") != 0:  # type 0 = text
                    continue
                bbox_raw = fitz_block.get("bbox", (0, 0, 0, 0))
                block_bbox = BBox(
                    x0=bbox_raw[0], y0=bbox_raw[1],
                    x1=bbox_raw[2], y1=bbox_raw[3],
                    page_number=pno,
                )
                spans: list[SpanIR] = []
                for line in fitz_block.get("lines", []):
                    for span in line.get("spans", []):
                        flags = span.get("flags", 0)
                        spans.append(SpanIR(
                            text=span.get("text", ""),
                            font_name=span.get("font", ""),
                            font_size=span.get("size", 12.0),
                            is_bold=bool(flags & 2**4),
                            is_italic=bool(flags & 2**1),
                            color=span.get("color"),
                        ))

                block = BlockIR(role=BlockRole.P, bbox=block_bbox, spans=spans)
                decision = classify_block(block_bbox, raw_page.height, raw_page.width)
                if decision.is_artifact:
                    block.is_artifact = True
                    block.role = BlockRole.ARTIFACT
                blocks.append(block)

        # Add OCR blocks for scanned pages
        for ocr_block in raw_page.ocr_blocks:
            block_bbox = BBox(
                x0=ocr_block["x0"], y0=ocr_block["y0"],
                x1=ocr_block["x1"], y1=ocr_block["y1"],
                page_number=pno,
            )
            span = SpanIR(text=ocr_block["text"])
            block = BlockIR(role=BlockRole.P, bbox=block_bbox, spans=[span])
            decision = classify_block(block_bbox, raw_page.height, raw_page.width)
            if decision.is_artifact:
                block.is_artifact = True
                block.role = BlockRole.ARTIFACT
            blocks.append(block)

        pages_ir.append(PageIR(
            page_number=pno,
            width=raw_page.width,
            height=raw_page.height,
            page_type=page_type,
            blocks=blocks,
        ))

    return pages_ir


def _map_docling_label(element) -> BlockRole:
    label = getattr(element, "label", None)
    if label is not None:
        label_str = label.value if hasattr(label, "value") else str(label)
        return _DOCLING_LABEL_MAP.get(label_str.lower(), BlockRole.P)
    return BlockRole.P
