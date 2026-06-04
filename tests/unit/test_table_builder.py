"""Tests for TableBuilder."""
import pytest
from tests.conftest import make_block
from ada_pdf.models.domain import BBox, BlockRole
from ada_pdf.pipeline.algorithms.table_builder import reconstruct


def _table_bbox(page: int = 1) -> BBox:
    return BBox(x0=50, y0=50, x1=560, y1=400, page_number=page)


@pytest.mark.unit
def test_simple_2x2_table():
    cells = [
        make_block("Name", x0=50, x1=200, y0=60, y1=80, is_bold=True),
        make_block("Age",  x0=300, x1=450, y0=60, y1=80, is_bold=True),
        make_block("Alice", x0=50, x1=200, y0=100, y1=120),
        make_block("30",    x0=300, x1=450, y0=100, y1=120),
    ]
    table = reconstruct(_table_bbox(), cells)
    assert len(table.header_rows) == 1
    assert len(table.body_rows) == 1
    assert len(table.header_rows[0].cells) == 2
    assert len(table.body_rows[0].cells) == 2


@pytest.mark.unit
def test_header_row_has_th_role():
    cells = [
        make_block("Col1", x0=50, x1=200, y0=60, y1=80, is_bold=True),
        make_block("Col2", x0=300, x1=450, y0=60, y1=80, is_bold=True),
        make_block("Data1", x0=50, x1=200, y0=100, y1=120),
        make_block("Data2", x0=300, x1=450, y0=100, y1=120),
    ]
    table = reconstruct(_table_bbox(), cells)
    for cell in table.header_rows[0].cells:
        assert cell.role == BlockRole.TH
        assert cell.scope == "col"


@pytest.mark.unit
def test_body_cells_have_td_role():
    cells = [
        make_block("H1", x0=50, x1=200, y0=60, y1=80, is_bold=True),
        make_block("H2", x0=300, x1=450, y0=60, y1=80, is_bold=True),
        make_block("V1", x0=50, x1=200, y0=100, y1=120),
        make_block("V2", x0=300, x1=450, y0=100, y1=120),
    ]
    table = reconstruct(_table_bbox(), cells)
    for cell in table.body_rows[0].cells:
        assert cell.role == BlockRole.TD


@pytest.mark.unit
def test_empty_table_region():
    table = reconstruct(_table_bbox(), [])
    assert table.header_rows == []
    assert table.body_rows == []


@pytest.mark.unit
def test_3_row_table():
    bold_cells = [
        make_block("Name", x0=50, x1=200, y0=60, y1=80, is_bold=True),
        make_block("Score", x0=300, x1=450, y0=60, y1=80, is_bold=True),
    ]
    data_cells = [
        make_block("Alice", x0=50, x1=200, y0=100, y1=120),
        make_block("95", x0=300, x1=450, y0=100, y1=120),
        make_block("Bob", x0=50, x1=200, y0=140, y1=160),
        make_block("87", x0=300, x1=450, y0=140, y1=160),
    ]
    table = reconstruct(_table_bbox(), bold_cells + data_cells)
    assert len(table.body_rows) == 2
