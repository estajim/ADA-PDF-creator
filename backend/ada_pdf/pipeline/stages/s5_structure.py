"""Stage 5 — Detect semantic structure: headings, lists, tables, reading order, forms."""
from __future__ import annotations

import re
import fitz

from ada_pdf.models.domain import (
    BlockIR, BlockRole, DocumentIR, FormFieldIR, PageIR, SpanIR, BBox,
)
from ada_pdf.pipeline.algorithms import (
    column_detector,
    heading_classifier,
    reading_order as reading_order_mod,
    table_builder,
)
from ada_pdf.pipeline.context import PipelineContext
from ada_pdf.config import Settings
from ada_pdf.utils.logging import get_logger

logger = get_logger(__name__)


def run(ctx: PipelineContext, settings: Settings) -> PipelineContext:
    logger.info("stage_start", stage="structure")
    pages_ir: list[PageIR] = ctx.layout_data
    doc = ctx.fitz_doc

    has_forms = False

    for page_ir in pages_ir:
        page = doc[page_ir.page_number - 1]

        # 1. Classify headings
        heading_classifier.classify(page_ir.blocks)

        # 2. Detect lists — group consecutive LI blocks into L containers
        _build_list_containers(page_ir)

        # 3. Reconstruct tables
        _reconstruct_tables(page_ir)

        # 4. Detect column layout and sort reading order
        zones = column_detector.detect(
            page_ir.blocks,
            page_ir.width,
            algorithm=settings.COLUMN_ALGORITHM.value,
        )
        reading_order_mod.sort_blocks(page_ir.blocks, zones, page_ir.width)

        # 5. Extract form fields
        form_fields = _extract_form_fields(page, page_ir.page_number)
        if form_fields:
            has_forms = True
            _attach_form_blocks(page_ir, form_fields)

    # Build language for document
    language = _detect_language(pages_ir)

    # Build title from first H1 block
    title = _extract_title(pages_ir)

    ctx.document_ir = DocumentIR(
        title=title,
        language=language,
        pages=pages_ir,
        has_forms=has_forms,
        original_filename=ctx.input_path.name,
    )

    # Extract metadata from PDF info dict
    if ctx.fitz_doc:
        meta = ctx.fitz_doc.metadata
        if meta:
            if meta.get("title"):
                ctx.document_ir.title = meta["title"]
            if meta.get("author"):
                ctx.document_ir.author = meta["author"]
            if meta.get("subject"):
                ctx.document_ir.subject = meta["subject"]
            if meta.get("creator"):
                ctx.document_ir.creator = meta["creator"]

    logger.info("stage_done", stage="structure", has_forms=has_forms, language=language)
    return ctx


def _build_list_containers(page_ir: PageIR) -> None:
    """Wrap consecutive LI blocks in parent L blocks."""
    new_blocks: list[BlockIR] = []
    current_list_items: list[BlockIR] = []

    def flush_list() -> None:
        if not current_list_items:
            return
        # Build a synthetic L block enclosing all items
        x0 = min(b.bbox.x0 for b in current_list_items)
        y0 = min(b.bbox.y0 for b in current_list_items)
        x1 = max(b.bbox.x1 for b in current_list_items)
        y1 = max(b.bbox.y1 for b in current_list_items)
        pno = current_list_items[0].bbox.page_number
        list_block = BlockIR(
            role=BlockRole.L,
            bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1, page_number=pno),
            children=list(current_list_items),
        )
        new_blocks.append(list_block)
        current_list_items.clear()

    for block in page_ir.blocks:
        if block.role == BlockRole.LI and not block.is_artifact:
            current_list_items.append(block)
        else:
            flush_list()
            new_blocks.append(block)
    flush_list()

    page_ir.blocks = new_blocks


def _reconstruct_tables(page_ir: PageIR) -> None:
    """Replace TABLE-role blocks with properly structured TableIR."""
    for block in page_ir.blocks:
        if block.role != BlockRole.TABLE or block.is_artifact:
            continue
        raw_blocks = [
            b for b in page_ir.blocks
            if b is not block and block.bbox.overlaps(b.bbox)
        ]
        block.table = table_builder.reconstruct(block.bbox, raw_blocks)
        # Remove raw cell blocks that were absorbed into the table
        absorbed = {id(b) for b in raw_blocks if block.bbox.overlaps(b.bbox)}
        page_ir.blocks = [
            b for b in page_ir.blocks
            if id(b) not in absorbed or b is block
        ]


_XFA_PATTERN = re.compile(r'^\w+\[\d+\](?:\.\w+\[\d+\])+$')
_FIELD_TYPE_LABELS = {
    "text": "Text field", "checkbox": "Checkbox",
    "radio": "Option", "select": "Select", "button": "Button",
}


def _extract_form_fields(page: fitz.Page, page_number: int) -> list[FormFieldIR]:
    # Pre-build a list of (text, rect) for nearby-label lookup
    # get_text("blocks") returns: (x0,y0,x1,y1, text, block_no, block_type)
    text_blocks = [
        (b[4].strip(), fitz.Rect(b[0], b[1], b[2], b[3]))
        for b in page.get_text("blocks")
        if len(b) >= 7 and b[6] == 0 and b[4].strip()  # type 0 = text block
    ] if page.get_text().strip() else []

    fields: list[FormFieldIR] = []
    field_type_map = {
        fitz.PDF_WIDGET_TYPE_TEXT: "text",
        fitz.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
        fitz.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
        fitz.PDF_WIDGET_TYPE_COMBOBOX: "select",
        fitz.PDF_WIDGET_TYPE_LISTBOX: "select",
        fitz.PDF_WIDGET_TYPE_BUTTON: "button",
    }

    for widget in page.widgets():
        ftype = field_type_map.get(widget.field_type, "text")
        raw_name = widget.field_name or ""
        rect = widget.rect

        # Resolve a human-readable label
        label = widget.field_label or ""
        if not label or _XFA_PATTERN.match(raw_name):
            # Try to find the nearest printed text to use as the label
            label = _nearest_text_label(rect, text_blocks) or _readable_name(raw_name, ftype)

        # Safe HTML id: strip XFA hierarchy, keep only the leaf identifier
        safe_id = re.sub(r'^\w+\[\d+\]\.', '', raw_name)  # strip prefix groups
        safe_id = re.sub(r'\[\d+\]', '', safe_id)          # strip index brackets
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', safe_id) or f"field_{len(fields)}"

        fields.append(FormFieldIR(
            field_name=safe_id,
            field_type=ftype,
            label=label,
            tooltip=label,
            tab_index=len(fields),
            bbox=BBox(x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1,
                      page_number=page_number),
        ))
    return fields


def _nearest_text_label(field_rect: fitz.Rect,
                        text_blocks: list[tuple[str, fitz.Rect]],
                        search_radius: float = 60) -> str:
    """Return the text of the nearest block to the left of or above the field."""
    best_text = ""
    best_dist = float("inf")
    fx, fy = field_rect.x0, (field_rect.y0 + field_rect.y1) / 2

    for text, brect in text_blocks:
        bx = brect.x1
        by = (brect.y0 + brect.y1) / 2
        # Only consider blocks to the left or above, within radius
        if bx > fx + 10:  # block starts to the right — skip
            continue
        dist = ((fx - bx) ** 2 + (fy - by) ** 2) ** 0.5
        if dist < best_dist and dist < search_radius:
            best_dist = dist
            best_text = text[:80].strip()

    return best_text


def _readable_name(raw_name: str, ftype: str) -> str:
    """Convert an XFA field name to a short human-readable label."""
    # Extract leaf: 'topmostSubform[0].Page1[0].f1_01[0]' → 'f1_01'
    leaf = raw_name.rsplit(".", 1)[-1]
    leaf = re.sub(r'\[\d+\]', '', leaf)  # remove [0]
    # Remove single-char type prefix if present: 'f1_01' → '1_01', 'c1_1' → '1_1'
    leaf = re.sub(r'^[a-z](\d)', r'\1', leaf)
    return f"{_FIELD_TYPE_LABELS.get(ftype, 'Field')} {leaf}"


def _attach_form_blocks(page_ir: PageIR, form_fields: list[FormFieldIR]) -> None:
    for ff in form_fields:
        block = BlockIR(
            role=BlockRole.FORM,
            bbox=ff.bbox,
            form_field=ff,
        )
        page_ir.blocks.append(block)


def _detect_language(pages_ir: list[PageIR]) -> str:
    text_sample = " ".join(
        span.text
        for page in pages_ir[:3]
        for block in page.blocks
        if not block.is_artifact
        for span in block.spans
    )[:2000]

    if not text_sample.strip():
        return "en"

    try:
        from lingua import Language, LanguageDetectorBuilder  # type: ignore[import]
        detector = (
            LanguageDetectorBuilder.from_all_languages()
            .with_minimum_relative_distance(0.15)
            .build()
        )
        lang = detector.detect_language_of(text_sample)
        if lang:
            return lang.iso_code_639_1.name.lower()
    except ImportError:
        pass
    except Exception:
        pass

    return "en"


def _extract_title(pages_ir: list[PageIR]) -> str:
    for page in pages_ir:
        for block in page.blocks:
            if block.role == BlockRole.H1 and block.text.strip():
                return block.text.strip()
    return "Untitled Document"
