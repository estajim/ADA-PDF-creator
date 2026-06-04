"""Reconstruct semantic table structure from raw bbox-positioned cell blocks."""
from statistics import median

from ada_pdf.models.domain import (
    BBox, BlockIR, BlockRole, CellIR, RowIR, SpanIR, TableIR,
)


def reconstruct(
    table_bbox: BBox,
    raw_blocks: list[BlockIR],
) -> TableIR:
    """Build a TableIR from raw text blocks within a table region.

    Steps:
    1. Filter blocks inside table_bbox.
    2. Cluster Y-centers → row boundaries.
    3. Cluster X-centers → column boundaries.
    4. Assign each block to a (row, col) grid position.
    5. Detect header rows (bold first row or first-row distinction).
    6. Detect merged cells (colspan/rowspan).
    7. Assign TH scope attributes.
    """
    cell_blocks = [
        b for b in raw_blocks
        if table_bbox.overlaps(b.bbox) and not b.is_artifact
    ]
    if not cell_blocks:
        return TableIR()

    # Cluster rows
    y_centers = sorted({round(b.bbox.y_center) for b in cell_blocks})
    row_boundaries = _cluster_1d(y_centers, gap_multiplier=0.5, font_size=_median_font_size(cell_blocks))

    # Cluster columns
    x_centers = sorted({round(b.bbox.x_center) for b in cell_blocks})
    col_boundaries = _cluster_1d(x_centers, gap_multiplier=2.0, font_size=_median_char_width(cell_blocks))

    # Grid: row index × col index → list of blocks
    grid: dict[tuple[int, int], list[BlockIR]] = {}
    for block in cell_blocks:
        r = _find_index(block.bbox.y_center, row_boundaries)
        c = _find_index(block.bbox.x_center, col_boundaries)
        grid.setdefault((r, c), []).append(block)

    n_rows = len(row_boundaries)
    n_cols = len(col_boundaries)

    avg_col_width = (table_bbox.width / n_cols) if n_cols > 0 else table_bbox.width

    rows: list[RowIR] = []
    for r_idx in range(n_rows):
        cells: list[CellIR] = []
        for c_idx in range(n_cols):
            blocks_in_cell = grid.get((r_idx, c_idx), [])
            spans = _merge_spans(blocks_in_cell)
            cell_bbox = _union_bbox(blocks_in_cell) if blocks_in_cell else None

            # Detect colspan
            colspan = 1
            if cell_bbox and avg_col_width > 0:
                colspan = max(1, round(cell_bbox.width / avg_col_width))

            # Determine cell role
            is_header_row = r_idx == 0 and _row_is_header(grid, n_cols)
            is_header_col = c_idx == 0 and _col_is_header(grid, n_rows)
            role = BlockRole.TH if (is_header_row or is_header_col) else BlockRole.TD

            scope = None
            if role == BlockRole.TH:
                scope = "col" if is_header_row else "row"

            cells.append(CellIR(spans=spans, role=role, scope=scope, colspan=colspan))

        is_header = _row_is_header(grid, n_cols) and r_idx == 0
        rows.append(RowIR(cells=cells, is_header=is_header))

    # Split into header and body
    header_rows = [r for r in rows if r.is_header]
    body_rows = [r for r in rows if not r.is_header]

    return TableIR(header_rows=header_rows, body_rows=body_rows)


# ── Helpers ────────────────────────────────────────────────────────────────

def _median_font_size(blocks: list[BlockIR]) -> float:
    sizes = [s.font_size for b in blocks for s in b.spans if s.font_size > 0]
    return median(sizes) if sizes else 10.0


def _median_char_width(blocks: list[BlockIR]) -> float:
    widths = []
    for b in blocks:
        text_len = len(b.text)
        if text_len > 0:
            widths.append(b.bbox.width / text_len)
    return median(widths) if widths else 8.0


def _cluster_1d(centers: list[float], gap_multiplier: float, font_size: float) -> list[float]:
    """Return cluster representative positions (one per cluster)."""
    if not centers:
        return []
    threshold = font_size * gap_multiplier
    clusters: list[list[float]] = [[centers[0]]]
    for c in centers[1:]:
        if c - clusters[-1][-1] > threshold:
            clusters.append([])
        clusters[-1].append(c)
    return [sum(cl) / len(cl) for cl in clusters]


def _find_index(value: float, cluster_centers: list[float]) -> int:
    if not cluster_centers:
        return 0
    return min(range(len(cluster_centers)), key=lambda i: abs(cluster_centers[i] - value))


def _merge_spans(blocks: list[BlockIR]) -> list[SpanIR]:
    spans: list[SpanIR] = []
    for b in sorted(blocks, key=lambda b: b.bbox.y0):
        spans.extend(b.spans)
    return spans


def _union_bbox(blocks: list[BlockIR]) -> BBox | None:
    if not blocks:
        return None
    x0 = min(b.bbox.x0 for b in blocks)
    y0 = min(b.bbox.y0 for b in blocks)
    x1 = max(b.bbox.x1 for b in blocks)
    y1 = max(b.bbox.y1 for b in blocks)
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1, page_number=blocks[0].bbox.page_number)


def _row_is_header(grid: dict[tuple[int, int], list[BlockIR]], n_cols: int) -> bool:
    """Check if row 0 contains bold spans (typical for table headers)."""
    for c in range(n_cols):
        for b in grid.get((0, c), []):
            if any(s.is_bold for s in b.spans):
                return True
    return False


def _col_is_header(grid: dict[tuple[int, int], list[BlockIR]], n_rows: int) -> bool:
    """Check if column 0 acts as row headers (bold cells in body rows, not just header row)."""
    if n_rows < 2:
        return False
    bold_count = 0
    # Start from row 1 to skip the column-header row (row 0)
    for r in range(1, n_rows):
        for b in grid.get((r, 0), []):
            if any(s.is_bold for s in b.spans):
                bold_count += 1
    body_rows = n_rows - 1
    return bold_count >= max(1, body_rows // 2)
