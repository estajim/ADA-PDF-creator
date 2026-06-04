"""Assign reading order via column-aware DAG topological sort."""
from collections import defaultdict, deque

from ada_pdf.models.domain import BlockIR
from ada_pdf.pipeline.algorithms.column_detector import ColumnZone, is_full_width


def sort_blocks(
    blocks: list[BlockIR],
    column_zones: list[ColumnZone],
    page_width: float,
) -> list[BlockIR]:
    """Return blocks sorted in logical reading order.

    Algorithm:
    1. Separate full-width blocks (anchors) from column blocks.
    2. Build a DAG where column blocks within the same zone get edges based on Y order.
    3. Full-width blocks are inserted as barriers at their Y position.
    4. Topological sort produces the final reading order.
    """
    if not blocks:
        return []

    # Full-width anchors only matter in multi-column layouts.
    # In a single-column document every block may span the full width — don't
    # treat them as column-breaking anchors, just sort them normally.
    multi_column = len(column_zones) > 1
    anchors = (
        [b for b in blocks if is_full_width(b, page_width) and not b.is_artifact]
        if multi_column else []
    )
    artifact_blocks = [b for b in blocks if b.is_artifact]

    # Sort anchors by their Y position
    anchors.sort(key=lambda b: b.bbox.y0)

    # Build ordering within each column zone
    ordered_col_blocks: list[BlockIR] = []
    for zone in column_zones:
        zone_blocks = sorted(zone.blocks, key=lambda b: b.bbox.y0)
        ordered_col_blocks.extend(zone_blocks)

    # Interleave anchors with column blocks using Y-position barriers
    result = _interleave_with_anchors(ordered_col_blocks, anchors)

    # Append artifacts at end (they won't enter the tag tree but need order for rendering)
    result.extend(artifact_blocks)

    # Assign reading_order integers
    for i, block in enumerate(result):
        block.reading_order = i

    return result


def _interleave_with_anchors(
    col_blocks: list[BlockIR],
    anchors: list[BlockIR],
) -> list[BlockIR]:
    """Insert full-width anchor blocks at their natural Y position among col_blocks."""
    result: list[BlockIR] = []
    anchor_idx = 0
    col_idx = 0

    while col_idx < len(col_blocks) or anchor_idx < len(anchors):
        anchor_y = anchors[anchor_idx].bbox.y0 if anchor_idx < len(anchors) else float("inf")
        col_y = col_blocks[col_idx].bbox.y0 if col_idx < len(col_blocks) else float("inf")

        if anchor_y <= col_y:
            result.append(anchors[anchor_idx])
            anchor_idx += 1
        else:
            result.append(col_blocks[col_idx])
            col_idx += 1

    return result
