"""Stage 1 — Load PDF and classify each page as DIGITAL, SCANNED, or MIXED."""
from __future__ import annotations

import fitz  # PyMuPDF

from ada_pdf.models.domain import PageType
from ada_pdf.pipeline.context import PipelineContext, RawPage
from ada_pdf.utils.logging import get_logger

logger = get_logger(__name__)

# A page is considered scanned if extracted text < this many characters
_TEXT_THRESHOLD = 20
# A page is considered mixed if it has text AND image area > this fraction of page
_IMAGE_AREA_THRESHOLD = 0.25


class IngestError(Exception):
    def __init__(self, message: str, is_recoverable: bool = False):
        self.is_recoverable = is_recoverable
        super().__init__(message)


def _rendered_image_coverage(page: fitz.Page, page_area: float) -> float:
    """Return the fraction of the page covered by rendered images.

    Uses the actual bounding boxes of image XObjects as placed on the page,
    not their intrinsic resolution (which inflates coverage for embedded images).
    Caps at 1.0 to handle overlapping images.
    """
    if page_area <= 0:
        return 0.0
    covered = 0.0
    try:
        for img_info in page.get_image_info(hashes=False):
            # img_info["bbox"] is the rendered rect on the page
            bbox = img_info.get("bbox")
            if bbox:
                w = abs(bbox[2] - bbox[0])
                h = abs(bbox[3] - bbox[1])
                covered += w * h
    except Exception:
        # Fall back to conservative estimate using raw image count
        n = len(page.get_images(full=False))
        covered = n * (page_area / max(n, 1)) * 0.1  # assume 10% each as safe fallback
    return min(covered / page_area, 1.0)


def run(ctx: PipelineContext, _settings) -> PipelineContext:
    logger.info("stage_start", stage="ingest", path=str(ctx.input_path))

    try:
        doc = fitz.open(str(ctx.input_path))
    except fitz.FileDataError as exc:
        msg = str(exc).lower()
        if "password" in msg or "encrypted" in msg:
            raise IngestError("PDF is password-protected. Remove encryption before uploading.")
        raise IngestError(f"Cannot open PDF: {exc}")

    ctx.fitz_doc = doc
    ctx.page_count = len(doc)
    ctx.raw_pages = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_width = page.rect.width
        page_height = page.rect.height

        text = page.get_text().strip()
        has_text = len(text) >= _TEXT_THRESHOLD

        # User override: skip auto-detection if doc_type is forced
        forced = getattr(ctx, "force_doc_type", None)
        if forced == "digital":
            page_type = PageType.DIGITAL
        elif forced == "scanned":
            page_type = PageType.SCANNED
        else:
            # Compute image coverage using *rendered* bounding boxes on the page,
            # not intrinsic pixel dimensions (which can be enormous for embedded XObjects).
            page_area = page_width * page_height
            image_coverage = _rendered_image_coverage(page, page_area)

            # A page with meaningful text is DIGITAL even if it has decorative images.
            # Only treat it as MIXED (needs OCR too) if text is sparse AND images dominate.
            if not has_text and image_coverage > _IMAGE_AREA_THRESHOLD:
                page_type = PageType.SCANNED
            elif has_text and image_coverage > _IMAGE_AREA_THRESHOLD and len(text) < 200:
                page_type = PageType.MIXED
            else:
                page_type = PageType.DIGITAL

        ctx.raw_pages.append(RawPage(
            page_number=page_num + 1,
            width=page_width,
            height=page_height,
        ))

        # Store page_type on the raw_page for later stages
        ctx.raw_pages[-1].__dict__["page_type"] = page_type

        logger.debug(
            "page_classified",
            page=page_num + 1,
            page_type=page_type.value,
            text_chars=len(text),
            image_coverage=round(image_coverage, 3),
        )

    logger.info("stage_done", stage="ingest", pages=ctx.page_count)
    return ctx
