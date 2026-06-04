"""Tests for HeadingClassifier."""
import pytest
from tests.conftest import make_block, make_page
from ada_pdf.models.domain import BlockRole
from ada_pdf.pipeline.algorithms.heading_classifier import classify


@pytest.mark.unit
def test_h1_large_font():
    blocks = [
        make_block("Big Title", font_size=28),
        make_block("Normal body text", font_size=12),
        make_block("Normal body text 2", font_size=12),
        make_block("Normal body text 3", font_size=12),
    ]
    classify(blocks)
    assert blocks[0].role == BlockRole.H1


@pytest.mark.unit
def test_h2_medium_font():
    blocks = [
        make_block("Section", font_size=20),   # 20/12 ≈ 1.67 → H2
        make_block("body", font_size=12),
        make_block("body", font_size=12),
    ]
    classify(blocks)
    assert blocks[0].role == BlockRole.H2


@pytest.mark.unit
def test_body_stays_p():
    blocks = [make_block("paragraph", font_size=12)] * 5
    classify(blocks)
    assert all(b.role == BlockRole.P for b in blocks)


@pytest.mark.unit
def test_all_caps_bold_becomes_h5():
    blocks = [
        make_block("SECTION LABEL", font_size=12, is_bold=True),
        make_block("body text", font_size=12),
        make_block("body text", font_size=12),
        make_block("body text", font_size=12),
        make_block("body text", font_size=12),
    ]
    classify(blocks)
    assert blocks[0].role == BlockRole.H5


@pytest.mark.unit
def test_hierarchy_fix_no_h3_without_h2():
    """H3 appearing before any H2 must be demoted to H2."""
    blocks = [
        make_block("Main", font_size=28),  # → H1
        make_block("Subsub", font_size=16),  # → H3 (ratio ~1.33), but no H2 yet → should become H2
        make_block("body", font_size=12),
        make_block("body", font_size=12),
        make_block("body", font_size=12),
    ]
    classify(blocks)
    # After hierarchy fix: H1, then next heading should be at most H2
    heading_roles = [b.role for b in blocks if b.is_heading]
    assert heading_roles[0] == BlockRole.H1
    # The second heading should not skip levels
    if len(heading_roles) > 1:
        level_second = int(heading_roles[1].value[1])
        assert level_second <= 2


@pytest.mark.unit
def test_pre_tagged_heading_preserved():
    """Blocks already tagged as headings (e.g. from Docling) should not be overwritten."""
    block = make_block("Already H3", font_size=12)
    block.role = BlockRole.H3  # pre-tagged
    other = [make_block("body", font_size=12)] * 3
    classify([block] + other)
    assert block.role == BlockRole.H3
