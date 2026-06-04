"""Detect header, footer, watermark, and decorative elements → Artifact."""
from dataclasses import dataclass

from ada_pdf.models.domain import BBox


@dataclass
class ArtifactDecision:
    is_artifact: bool
    reason: str


_HEADER_THRESHOLD = 0.08  # top 8% of page height
_FOOTER_THRESHOLD = 0.92  # bottom 8% of page height


def classify_block(bbox: BBox, page_height: float, page_width: float) -> ArtifactDecision:
    """Return whether a block at bbox should be marked as a PDF Artifact."""
    y_top_pct = bbox.y0 / page_height
    y_bot_pct = bbox.y1 / page_height

    # Header zone
    if y_bot_pct <= _HEADER_THRESHOLD:
        return ArtifactDecision(is_artifact=True, reason="header_zone")

    # Footer zone
    if y_top_pct >= _FOOTER_THRESHOLD:
        return ArtifactDecision(is_artifact=True, reason="footer_zone")

    # Full-page-width hairline (horizontal rule used as decoration) — check before degenerate
    if bbox.width >= page_width * 0.85 and bbox.height <= 3:
        return ArtifactDecision(is_artifact=True, reason="decorative_rule")

    # Near-zero width or height (invisible rule/line element)
    if bbox.width < 2 or bbox.height < 2:
        return ArtifactDecision(is_artifact=True, reason="degenerate_bbox")

    return ArtifactDecision(is_artifact=False, reason="")


def is_image_decorative(width_px: float, height_px: float, aspect_ratio_threshold: float = 10.0) -> bool:
    """Small images (< 50×50 px) or extreme aspect ratios are treated as decorative."""
    if width_px < 50 and height_px < 50:
        return True
    if width_px > 0 and height_px > 0:
        ar = max(width_px, height_px) / min(width_px, height_px)
        if ar > aspect_ratio_threshold:
            return True
    return False
