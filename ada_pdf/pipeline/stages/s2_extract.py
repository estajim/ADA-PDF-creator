"""Stage 2 — Extract native text (digital pages) and rasterize (scanned pages)."""
from __future__ import annotations

import fitz

from ada_pdf.models.domain import PageType
from ada_pdf.pipeline.context import PipelineContext
from ada_pdf.utils.logging import get_logger

logger = get_logger(__name__)

_OCR_DPI = 150   # 300 DPI produces huge images that crash Tesseract on complex pages
_FITZ_TEXT_FLAGS = fitz.TEXT_PRESERVE_SPANS | fitz.TEXT_PRESERVE_WHITESPACE


def run(ctx: PipelineContext, _settings) -> PipelineContext:
    logger.info("stage_start", stage="extract")
    doc = ctx.fitz_doc

    for raw_page in ctx.raw_pages:
        page_type = raw_page.__dict__.get("page_type", PageType.DIGITAL)
        page = doc[raw_page.page_number - 1]

        if page_type == PageType.DIGITAL:
            raw_page.raw_fitz_dict = page.get_text("dict", flags=_FITZ_TEXT_FLAGS)
        elif page_type == PageType.SCANNED:
            raw_page.image_bytes = _rasterize(page)
        else:  # MIXED
            raw_page.raw_fitz_dict = page.get_text("dict", flags=_FITZ_TEXT_FLAGS)
            raw_page.image_bytes = _rasterize(page)

    logger.info("stage_done", stage="extract")
    return ctx


def _rasterize(page: fitz.Page) -> bytes:
    mat = fitz.Matrix(_OCR_DPI / 72, _OCR_DPI / 72)
    pixmap = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
    return pixmap.tobytes("png")
