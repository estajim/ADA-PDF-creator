"""Tests for ArtifactDetector."""
import pytest
from ada_pdf.models.domain import BBox
from ada_pdf.pipeline.algorithms.artifact_detector import classify_block, is_image_decorative

PAGE_H = 792.0
PAGE_W = 612.0


def _bbox(y0, y1, x0=50, x1=560):
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1, page_number=1)


@pytest.mark.unit
def test_header_zone():
    # Top 8% = 63.36 px — block ending at y=60 is in header zone
    result = classify_block(_bbox(y0=10, y1=60), PAGE_H, PAGE_W)
    assert result.is_artifact
    assert result.reason == "header_zone"


@pytest.mark.unit
def test_footer_zone():
    # Bottom 8% starts at 792 * 0.92 = 728.64
    result = classify_block(_bbox(y0=740, y1=780), PAGE_H, PAGE_W)
    assert result.is_artifact
    assert result.reason == "footer_zone"


@pytest.mark.unit
def test_body_block_not_artifact():
    result = classify_block(_bbox(y0=200, y1=220), PAGE_H, PAGE_W)
    assert not result.is_artifact


@pytest.mark.unit
def test_degenerate_bbox():
    result = classify_block(_bbox(y0=300, y1=301, x0=100, x1=101), PAGE_H, PAGE_W)
    assert result.is_artifact
    assert result.reason == "degenerate_bbox"


@pytest.mark.unit
def test_decorative_rule():
    # Full-width hairline rule
    result = classify_block(_bbox(y0=400, y1=401, x0=10, x1=600), PAGE_H, PAGE_W)
    assert result.is_artifact
    assert result.reason == "decorative_rule"


@pytest.mark.unit
def test_image_decorative_small():
    assert is_image_decorative(30, 30)


@pytest.mark.unit
def test_image_decorative_wide_aspect():
    assert is_image_decorative(1000, 5)


@pytest.mark.unit
def test_image_not_decorative():
    assert not is_image_decorative(200, 150)
