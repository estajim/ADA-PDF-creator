"""Tests for ReadingOrderSorter."""
import pytest
from tests.conftest import make_block
from ada_pdf.pipeline.algorithms.column_detector import detect
from ada_pdf.pipeline.algorithms.reading_order import sort_blocks

PAGE_W = 612.0


@pytest.mark.unit
def test_single_column_order():
    b1 = make_block("A", x0=50, x1=560, y0=100, y1=120)
    b2 = make_block("B", x0=50, x1=560, y0=130, y1=150)
    b3 = make_block("C", x0=50, x1=560, y0=160, y1=180)
    blocks = [b3, b1, b2]  # deliberately shuffled
    zones = detect(blocks, PAGE_W)
    result = sort_blocks(blocks, zones, PAGE_W)
    texts = [b.text for b in result if not b.is_artifact]
    assert texts == ["A", "B", "C"]


@pytest.mark.unit
def test_two_column_left_first():
    """Left column should be read fully before right column."""
    l1 = make_block("L1", x0=50, x1=250, y0=100, y1=120)
    l2 = make_block("L2", x0=50, x1=250, y0=130, y1=150)
    r1 = make_block("R1", x0=350, x1=560, y0=100, y1=120)
    r2 = make_block("R2", x0=350, x1=560, y0=130, y1=150)

    blocks = [r1, l2, r2, l1]
    zones = detect(blocks, PAGE_W)
    result = sort_blocks(blocks, zones, PAGE_W)
    texts = [b.text for b in result if not b.is_artifact]
    assert texts.index("L1") < texts.index("L2")
    assert texts.index("L2") < texts.index("R1")
    assert texts.index("R1") < texts.index("R2")


@pytest.mark.unit
def test_full_width_anchor_splits_columns():
    """A full-width block between column content should appear at the right position."""
    l1 = make_block("L1", x0=50, x1=250, y0=100, y1=120)
    full = make_block("Heading", x0=20, x1=590, y0=150, y1=170)  # full-width
    l2 = make_block("L2", x0=50, x1=250, y0=200, y1=220)

    blocks = [l2, full, l1]
    zones = detect(blocks, PAGE_W)
    result = sort_blocks(blocks, zones, PAGE_W)
    texts = [b.text for b in result if not b.is_artifact]
    assert texts.index("L1") < texts.index("Heading")
    assert texts.index("Heading") < texts.index("L2")


@pytest.mark.unit
def test_reading_order_integers_assigned():
    blocks = [make_block(f"P{i}", y0=i * 20, y1=i * 20 + 15) for i in range(5)]
    zones = detect(blocks, PAGE_W)
    sort_blocks(blocks, zones, PAGE_W)
    orders = [b.reading_order for b in blocks if not b.is_artifact]
    assert sorted(orders) == list(range(len(orders)))
