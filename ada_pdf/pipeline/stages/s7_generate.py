"""Stage 7 — Generate tagged PDF/UA from DocumentIR via WeasyPrint (primary) or ReportLab (fallback)."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from jinja2 import Environment, PackageLoader, select_autoescape

from ada_pdf.models.domain import (
    BlockIR, BlockRole, CellIR, DocumentIR, PageIR, RowIR, TableIR,
)
from ada_pdf.pipeline.context import PipelineContext
from ada_pdf.config import Settings
from ada_pdf.utils.logging import get_logger

logger = get_logger(__name__)


class GenerationError(Exception):
    pass


# Jinja2 environment — templates live in ada_pdf/templates/
_JINJA_ENV = Environment(
    loader=PackageLoader("ada_pdf", "templates"),
    autoescape=select_autoescape(["html"]),
)


def run(ctx: PipelineContext, settings: Settings) -> PipelineContext:
    logger.info("stage_start", stage="generate")

    doc_ir: DocumentIR = ctx.document_ir
    html = _render_html(doc_ir)

    if settings.WEASYPRINT_ENABLED:
        pdf_bytes = _generate_weasyprint(html, settings.WEASYPRINT_TIMEOUT_SECONDS)
        if pdf_bytes:
            ctx.output_pdf_bytes = pdf_bytes
            logger.info("stage_done", stage="generate", generator="weasyprint")
            return ctx
        logger.warning("weasyprint_failed", note="trying reportlab fallback")

    if settings.REPORTLAB_FALLBACK:
        pdf_bytes = _generate_reportlab(doc_ir)
        if pdf_bytes:
            ctx.output_pdf_bytes = pdf_bytes
            logger.info("stage_done", stage="generate", generator="reportlab")
            return ctx

    raise GenerationError("Both WeasyPrint and ReportLab generators failed")


def _render_html(doc_ir: DocumentIR) -> str:
    """Render DocumentIR to semantic HTML for WeasyPrint."""
    try:
        template = _JINJA_ENV.get_template("document.html")
        return template.render(doc=doc_ir)
    except Exception:
        # Fallback: generate HTML programmatically
        return _render_html_inline(doc_ir)


def _render_html_inline(doc_ir: DocumentIR) -> str:
    """Pure-Python HTML rendering — no template files needed."""
    parts = [
        '<!DOCTYPE html>',
        '<html lang="{lang}">'.format(lang=doc_ir.language),
        '<head>',
        '<meta charset="UTF-8"/>',
        '<title>{title}</title>'.format(title=_esc(doc_ir.title)),
        '<style>',
        'body { font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.4; margin: 2cm; }',
        'h1 { font-size: 2em; } h2 { font-size: 1.6em; } h3 { font-size: 1.3em; }',
        'h4 { font-size: 1.1em; } h5 { font-size: 1em; font-weight: bold; }',
        'table { border-collapse: collapse; width: 100%; margin: 1em 0; }',
        'th, td { border: 1px solid #ccc; padding: 0.4em 0.6em; text-align: left; }',
        'th { background: #f0f0f0; font-weight: bold; }',
        'figure { margin: 1em 0; }',
        'figure img { max-width: 100%; }',
        '</style>',
        '</head>',
        '<body>',
        '<main role="main">',
    ]

    for page_ir in doc_ir.pages:
        for block in sorted(page_ir.blocks, key=lambda b: b.reading_order):
            if block.is_artifact:
                continue
            parts.append(_render_block(block))

    parts += ['</main>', '</body>', '</html>']
    return "\n".join(parts)


def _render_block(block: BlockIR) -> str:
    role = block.role
    text = _esc(block.text)

    if role == BlockRole.H1:
        return f"<h1>{text}</h1>"
    if role == BlockRole.H2:
        return f"<h2>{text}</h2>"
    if role == BlockRole.H3:
        return f"<h3>{text}</h3>"
    if role == BlockRole.H4:
        return f"<h4>{text}</h4>"
    if role == BlockRole.H5:
        return f"<h5>{text}</h5>"
    if role == BlockRole.H6:
        return f"<h6>{text}</h6>"
    if role == BlockRole.P:
        lang_attr = f' lang="{block.lang}"' if block.lang else ""
        return f"<p{lang_attr}>{text}</p>"
    if role == BlockRole.L:
        items = "".join(f"<li>{_esc(c.text)}</li>" for c in block.children)
        return f"<ul>{items}</ul>"
    if role == BlockRole.FIGURE:
        alt = _esc(block.alt_text or "")
        return f'<figure><img src="" alt="{alt}" role="img"/></figure>'
    if role == BlockRole.CAPTION:
        return f"<figcaption>{text}</figcaption>"
    if role == BlockRole.TABLE and block.table:
        return _render_table(block.table)
    if role == BlockRole.FORM and block.form_field:
        ff = block.form_field
        return (
            f'<label for="{_esc(ff.field_name)}">{_esc(ff.label)}</label> '
            f'<input type="{_esc(ff.field_type)}" id="{_esc(ff.field_name)}" '
            f'name="{_esc(ff.field_name)}" title="{_esc(ff.tooltip)}"/>'
        )
    if role == BlockRole.LINK:
        return f'<a href="#">{text}</a>'
    return f"<p>{text}</p>"


def _render_table(table: TableIR) -> str:
    parts = ["<table>"]
    if table.header_rows:
        parts.append("<thead>")
        for row in table.header_rows:
            parts.append(_render_row(row))
        parts.append("</thead>")
    if table.body_rows:
        parts.append("<tbody>")
        for row in table.body_rows:
            parts.append(_render_row(row))
        parts.append("</tbody>")
    parts.append("</table>")
    return "\n".join(parts)


def _render_row(row: RowIR) -> str:
    cells = []
    for cell in row.cells:
        tag = "th" if cell.role == BlockRole.TH else "td"
        attrs = ""
        if cell.scope:
            attrs += f' scope="{cell.scope}"'
        if cell.colspan > 1:
            attrs += f' colspan="{cell.colspan}"'
        if cell.rowspan > 1:
            attrs += f' rowspan="{cell.rowspan}"'
        cells.append(f"<{tag}{attrs}>{_esc(cell.text)}</{tag}>")
    return "<tr>" + "".join(cells) + "</tr>"


def _esc(text: str) -> str:
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _generate_weasyprint(html: str, timeout: int) -> Optional[bytes]:
    try:
        import signal
        from weasyprint import HTML, CSS  # type: ignore[import]

        def _timeout_handler(signum, frame):
            raise TimeoutError("WeasyPrint timed out")

        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout)
        try:
            pdf_bytes = HTML(string=html).write_pdf(
                pdf_variant="pdf/ua-1",
                uncompressed_pdf=False,
            )
        finally:
            signal.alarm(0)
        return pdf_bytes
    except TimeoutError:
        logger.warning("weasyprint_timeout")
        return None
    except ImportError:
        logger.warning("weasyprint_not_installed")
        return None
    except Exception as exc:
        logger.warning("weasyprint_error", error=str(exc))
        return None


def _generate_reportlab(doc_ir: DocumentIR) -> Optional[bytes]:
    """ReportLab fallback — generates a basic tagged PDF."""
    try:
        import io as _io
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle  # type: ignore[import]
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle  # type: ignore[import]
        from reportlab.lib.pagesizes import letter  # type: ignore[import]
        from reportlab.lib import colors  # type: ignore[import]

        buf = _io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter, title=doc_ir.title, author=doc_ir.author or "")
        styles = getSampleStyleSheet()
        story = []

        heading_styles = {
            BlockRole.H1: styles["Heading1"],
            BlockRole.H2: styles["Heading2"],
            BlockRole.H3: styles["Heading3"],
            BlockRole.H4: styles["Heading4"],
        }

        for page_ir in doc_ir.pages:
            for block in sorted(page_ir.blocks, key=lambda b: b.reading_order):
                if block.is_artifact:
                    continue
                if block.is_heading and block.role in heading_styles:
                    story.append(Paragraph(block.text, heading_styles[block.role]))
                elif block.role == BlockRole.P:
                    story.append(Paragraph(block.text, styles["Normal"]))
                elif block.role == BlockRole.TABLE and block.table:
                    tdata = []
                    for row in block.table.header_rows + block.table.body_rows:
                        tdata.append([c.text for c in row.cells])
                    if tdata:
                        tbl = Table(tdata)
                        tbl.setStyle(TableStyle([
                            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                        ]))
                        story.append(tbl)
                story.append(Spacer(1, 6))

        doc.build(story)
        return buf.getvalue()
    except ImportError:
        logger.warning("reportlab_not_installed")
        return None
    except Exception as exc:
        logger.warning("reportlab_error", error=str(exc))
        return None
