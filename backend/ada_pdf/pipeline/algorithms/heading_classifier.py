"""Classify text blocks as H1–H6 based on font size and weight."""
from collections import Counter

from ada_pdf.models.domain import BlockIR, BlockRole, SpanIR


def _dominant_span(block: BlockIR) -> SpanIR | None:
    if not block.spans:
        return None
    return max(block.spans, key=lambda s: len(s.text))


def classify(blocks: list[BlockIR]) -> None:
    """Mutate BlockIR.role in-place to assign heading levels H1–H6.

    Rules (applied in order of precedence):
    1. If Docling already tagged a block as a heading, trust that.
    2. Derive body font size from the mode of all span sizes.
    3. Apply size-ratio tiers to identify H1–H4.
    4. All-caps + bold at body size → H5.
    5. Validate hierarchy: flatten any skipped heading levels.
    """
    text_blocks = [b for b in blocks if not b.is_artifact and b.spans]
    if not text_blocks:
        return

    # Step 1: collect all font sizes weighted by text length
    size_counts: Counter[float] = Counter()
    for block in text_blocks:
        for span in block.spans:
            rounded = round(span.font_size * 2) / 2  # bucket to 0.5pt steps
            size_counts[rounded] += len(span.text)

    if not size_counts:
        return

    body_size = size_counts.most_common(1)[0][0]

    # Step 2: classify each non-artifact paragraph block that is not already a heading
    for block in text_blocks:
        if block.is_heading:
            continue  # already set (e.g. by Docling layout override)
        if block.role != BlockRole.P:
            continue

        span = _dominant_span(block)
        if span is None:
            continue

        ratio = span.font_size / body_size if body_size > 0 else 1.0

        if ratio >= 2.0:
            block.role = BlockRole.H1
        elif ratio >= 1.6:
            block.role = BlockRole.H2
        elif ratio >= 1.3:
            block.role = BlockRole.H3
        elif ratio >= 1.1:
            block.role = BlockRole.H4
        elif span.is_bold and block.text == block.text.upper() and len(block.text.strip()) > 0:
            block.role = BlockRole.H5
        elif span.is_italic and ratio >= 0.95:
            block.role = BlockRole.H6

    # Step 3: fix hierarchy — no H3 without preceding H2, etc.
    _fix_heading_hierarchy(blocks)


_HEADING_LEVELS = {
    BlockRole.H1: 1,
    BlockRole.H2: 2,
    BlockRole.H3: 3,
    BlockRole.H4: 4,
    BlockRole.H5: 5,
    BlockRole.H6: 6,
}
_LEVEL_TO_ROLE = {v: k for k, v in _HEADING_LEVELS.items()}


def _fix_heading_hierarchy(blocks: list[BlockIR]) -> None:
    """Enforce PDF/UA heading rules:
    1. The first heading in the document must be H1.
    2. No heading level may be skipped when descending (H1→H3 is invalid).
    """
    heading_blocks = [b for b in blocks if b.is_heading]
    if not heading_blocks:
        return

    # Rule 1: if first heading isn't H1, shift the entire hierarchy down
    first_level = _HEADING_LEVELS[heading_blocks[0].role]
    if first_level != 1:
        shift = first_level - 1
        for b in heading_blocks:
            new_level = max(1, _HEADING_LEVELS[b.role] - shift)
            b.role = _LEVEL_TO_ROLE[new_level]

    # Rule 2: no skipped levels when descending
    current_max = 0
    for block in heading_blocks:
        level = _HEADING_LEVELS[block.role]
        if current_max > 0 and level > current_max + 1:
            level = current_max + 1
            block.role = _LEVEL_TO_ROLE[level]
        current_max = max(current_max, level)
