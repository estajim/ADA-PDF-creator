"""Tests for ColumnDetector."""
import pytest
from tests.conftest import make_block
from ada_pdf.pipeline.algorithms.column_detector import detect, is_full_width

PAGE_W = 612.0


@pytest.mark.unit
def test_single_column():
    blocks = [
        make_block("A", x0=50, x1=560, y0=100, y1=120),
        make_block("B", x0=50, x1=560, y0=130, y1=150),
    ]
    zones = detect(blocks, PAGE_W)
    assert len(zones) == 1


@pytest.mark.unit
def test_two_columns_detected():
    blocks = [
        make_block("L1", x0=50, x1=250, y0=100, y1=120),
        make_block("L2", x0=50, x1=250, y0=130, y1=150),
        make_block("R1", x0=350, x1=560, y0=100, y1=120),
        make_block("R2", x0=350, x1=560, y0=130, y1=150),
    ]
    zones = detect(blocks, PAGE_W)
    assert len(zones) == 2


@pytest.mark.unit
def test_two_column_assignment():
    left1 = make_block("L1", x0=50, x1=250, y0=100, y1=120)
    left2 = make_block("L2", x0=50, x1=250, y0=130, y1=150)
    right1 = make_block("R1", x0=350, x1=560, y0=100, y1=120)
    right2 = make_block("R2", x0=350, x1=560, y0=130, y1=150)

    zones = detect([left1, left2, right1, right2], PAGE_W)
    assert len(zones) == 2

    left_zone_texts = {b.text for b in zones[0].blocks}
    right_zone_texts = {b.text for b in zones[1].blocks}
    assert left_zone_texts == {"L1", "L2"}
    assert right_zone_texts == {"R1", "R2"}


@pytest.mark.unit
def test_artifact_blocks_excluded():
    artifact = make_block("Header", x0=50, x1=560, y0=10, y1=30, is_artifact=True)
    body = make_block("Body", x0=50, x1=560, y0=100, y1=120)
    zones = detect([artifact, body], PAGE_W)
    # Artifact should not appear in column zones
    for zone in zones:
        assert artifact not in zone.blocks


@pytest.mark.unit
def test_full_width_detection():
    wide = make_block("Wide", x0=20, x1=590)  # 570px of 612px = 93% → full width
    narrow = make_block("Narrow", x0=50, x1=250)
    assert is_full_width(wide, PAGE_W)
    assert not is_full_width(narrow, PAGE_W)
