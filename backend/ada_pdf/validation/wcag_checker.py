"""WCAG 2.1 AA checks on converted PDFs.

These checks go beyond veraPDF's PDF/UA-1 structural validation and target
the most common real-world accessibility failures: color contrast, link text
quality, missing document title, and reading order heuristics.

All checks return WCAGIssue objects.  They are advisory — they do not cause
the job to be marked 'partial' but they are surfaced in the report so users
know what to fix manually when automated repair is insufficient.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class WCAGIssue(BaseModel):
    criterion: str                        # e.g. "1.4.3"
    level: Literal["A", "AA", "AAA"]
    title: str                            # Short criterion name
    severity: Literal["error", "warning", "info"]
    description: str                      # What was found
    fix: str                              # Plain-English fix instruction
    location: str = ""


def run_wcag_checks(pdf_path: Path) -> list[WCAGIssue]:
    """Run all WCAG 2.1 AA checks against the output PDF. Returns a list of issues."""
    issues: list[WCAGIssue] = []
    try:
        import pikepdf
        with pikepdf.open(str(pdf_path)) as pdf:
            issues += _check_document_title(pdf)
            issues += _check_language(pdf)
            issues += _check_link_text(pdf)
            issues += _check_color_contrast(pdf)
            issues += _check_image_alt_text(pdf)
            issues += _check_form_labels(pdf)
            issues += _check_reading_order(pdf)
            issues += _check_document_structure(pdf)
    except Exception:
        pass
    return issues


# ── 2.4.2 Page Titled ────────────────────────────────────────────────────────

def _check_document_title(pdf) -> list[WCAGIssue]:
    issues = []
    title = None
    try:
        title = str(pdf.docinfo.get("/Title", "")).strip()
    except Exception:
        pass
    if not title:
        try:
            with pdf.open_metadata() as meta:
                title = meta.get("dc:title", "")
        except Exception:
            pass
    if not title:
        issues.append(WCAGIssue(
            criterion="2.4.2", level="A", title="Page Titled",
            severity="error",
            description="Document has no title set.",
            fix="Set the PDF title in the document properties. In code: pdf.docinfo['/Title'] = 'Your Title'.",
        ))
    return issues


# ── 3.1.1 Language of Page ────────────────────────────────────────────────────

def _check_language(pdf) -> list[WCAGIssue]:
    issues = []
    try:
        lang = str(pdf.Root.get("/Lang", "")).strip()
        if not lang:
            issues.append(WCAGIssue(
                criterion="3.1.1", level="A", title="Language of Page",
                severity="error",
                description="Document language (/Lang) is not set in the document catalog.",
                fix="Set pdf.Root.Lang = pikepdf.String('en-US') (or the appropriate BCP-47 tag).",
            ))
    except Exception:
        pass
    return issues


# ── 2.4.4 Link Purpose (In Context) ──────────────────────────────────────────

_GENERIC = frozenset({
    "click here", "click", "here", "read more", "read more »", "more",
    "learn more", "link", "this link", "this", "go", "continue", "details",
    "see details", "info", "more info", "more information", "visit",
    "download", "get it", "open", "view", "see more", "see here",
})


def _check_link_text(pdf) -> list[WCAGIssue]:
    import pikepdf

    def _deref(obj):
        if obj is None:
            return None
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Array)):
            return obj
        if hasattr(obj, 'objgen') and obj.objgen != (0, 0):
            try:
                return pdf.get_object(obj.objgen)
            except Exception:
                pass
        return obj

    def _get_text(obj, depth=0) -> str:
        if depth > 6:
            return ""
        try:
            t = obj.get("/T") or obj.get("/ActualText") or obj.get("/Alt")
            if t:
                return str(t).strip()
            k = _deref(obj.get("/K"))
            if isinstance(k, pikepdf.Array):
                parts = []
                for child in list(k)[:8]:
                    if isinstance(child, int):
                        continue
                    c = _deref(child)
                    if isinstance(c, pikepdf.Dictionary):
                        parts.append(_get_text(c, depth + 1))
                return " ".join(p for p in parts if p)
        except Exception:
            pass
        return ""

    bad_links: list[str] = []

    def _walk(node, depth=0):
        if depth > 60:
            return
        try:
            obj = _deref(node)
            if not isinstance(obj, pikepdf.Dictionary):
                return
            s = str(obj.get("/S", ""))
            if s == "/Link":
                text = _get_text(obj).lower().strip(".,!? ")
                if text in _GENERIC:
                    bad_links.append(repr(text))
                return
            k = obj.get("/K")
            if k is None:
                return
            if isinstance(k, pikepdf.Array):
                for child in list(k):
                    if not isinstance(child, int):
                        _walk(child, depth + 1)
            elif not isinstance(k, int):
                _walk(k, depth + 1)
        except Exception:
            pass

    try:
        struct = pdf.Root.get("/StructTreeRoot")
        if struct:
            k = _deref(struct).get("/K") if hasattr(struct, 'get') else None
            if isinstance(k, pikepdf.Array):
                for child in list(k):
                    _walk(child)
            elif k is not None:
                _walk(k)
    except Exception:
        pass

    issues = []
    if bad_links:
        sample = ", ".join(bad_links[:5])
        issues.append(WCAGIssue(
            criterion="2.4.4", level="AA", title="Link Purpose (In Context)",
            severity="warning",
            description=f"Found {len(bad_links)} link(s) with generic text: {sample}.",
            fix="Replace generic link text ('click here', 'read more') with a description "
                "of the link's destination or purpose. Screen reader users navigate by link text.",
            location=f"{len(bad_links)} link elements",
        ))
    return issues


# ── 1.4.3 Contrast (Minimum) ─────────────────────────────────────────────────

def _relative_luminance(r: float, g: float, b: float) -> float:
    """WCAG relative luminance from linear sRGB."""
    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _contrast_ratio(l1: float, l2: float) -> float:
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _check_color_contrast(pdf) -> list[WCAGIssue]:
    """Sample text operations in content streams to detect low-contrast text."""
    import pikepdf

    NORMAL_MIN = 4.5
    LARGE_MIN = 3.0
    LARGE_PT = 18.0
    LARGE_BOLD_PT = 14.0

    low_contrast_count = 0
    total_sampled = 0

    for page_idx, page in enumerate(pdf.pages):
        if page_idx > 4:  # Sample first 5 pages — representative without full scan
            break
        try:
            page_obj = page.obj if hasattr(page, 'obj') else page
            resources = page_obj.get("/Resources")
            if resources:
                res = pdf.get_object(resources.objgen) if hasattr(resources, 'objgen') else resources
            else:
                res = pikepdf.Dictionary()

            # Current color state
            fill_color = (0.0, 0.0, 0.0)    # default black
            stroke_color = (0.0, 0.0, 0.0)
            font_size = 12.0
            is_bold = False
            # Page background is assumed white (most common)
            bg_lum = _relative_luminance(1.0, 1.0, 1.0)

            instructions = list(pikepdf.parse_content_stream(page))
            for operands, op in instructions:
                op_str = str(op)

                if op_str == "Tf" and len(operands) >= 2:
                    try:
                        font_size = abs(float(operands[1]))
                        font_name = str(operands[0])
                        is_bold = "Bold" in font_name or "bold" in font_name
                    except Exception:
                        pass

                elif op_str in ("rg", "RG") and len(operands) >= 3:
                    try:
                        rgb = (float(operands[0]), float(operands[1]), float(operands[2]))
                        if op_str == "rg":
                            fill_color = rgb
                        else:
                            stroke_color = rgb
                    except Exception:
                        pass

                elif op_str == "g" and len(operands) >= 1:
                    try:
                        v = float(operands[0])
                        fill_color = (v, v, v)
                    except Exception:
                        pass

                elif op_str in ("Tj", "TJ", "'", '"') :
                    # Text show operation — sample contrast here
                    if font_size < 1:
                        continue
                    total_sampled += 1
                    fg_lum = _relative_luminance(*fill_color)
                    ratio = _contrast_ratio(fg_lum, bg_lum)
                    is_large = (font_size >= LARGE_PT or
                                (is_bold and font_size >= LARGE_BOLD_PT))
                    min_ratio = LARGE_MIN if is_large else NORMAL_MIN
                    if ratio < min_ratio:
                        low_contrast_count += 1

        except Exception:
            continue

    issues = []
    if total_sampled > 0:
        fail_rate = low_contrast_count / total_sampled
        if low_contrast_count > 0:
            severity = "error" if fail_rate > 0.1 else "warning"
            issues.append(WCAGIssue(
                criterion="1.4.3", level="AA", title="Contrast (Minimum)",
                severity=severity,
                description=(
                    f"Detected {low_contrast_count} of {total_sampled} sampled text operations "
                    f"with insufficient contrast ratio (min 4.5:1 normal, 3:1 large text). "
                    f"Sample covers first 5 pages."
                ),
                fix="Increase text/background contrast. Use a contrast checker tool. "
                    "Common issues: light grey text on white, coloured text without sufficient contrast.",
                location=f"~{low_contrast_count} operations across first 5 pages",
            ))
    return issues


# ── 1.1.1 Non-text Content (Images missing alt text) ─────────────────────────

def _check_image_alt_text(pdf) -> list[WCAGIssue]:
    import pikepdf

    def _deref(obj):
        if obj is None:
            return None
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Array)):
            return obj
        if hasattr(obj, 'objgen') and obj.objgen != (0, 0):
            try:
                return pdf.get_object(obj.objgen)
            except Exception:
                pass
        return obj

    missing_alt = 0

    def _walk(node, depth=0):
        nonlocal missing_alt
        if depth > 60:
            return
        try:
            obj = _deref(node)
            if not isinstance(obj, pikepdf.Dictionary):
                return
            s = str(obj.get("/S", ""))
            if s == "/Figure":
                alt = obj.get("/Alt")
                actual = obj.get("/ActualText")
                if not alt and not actual:
                    missing_alt += 1
                return
            k = obj.get("/K")
            if k is None:
                return
            if isinstance(k, pikepdf.Array):
                for child in list(k):
                    if not isinstance(child, int):
                        _walk(child, depth + 1)
            elif not isinstance(k, int):
                _walk(k, depth + 1)
        except Exception:
            pass

    try:
        struct = pdf.Root.get("/StructTreeRoot")
        if struct:
            k = _deref(struct).get("/K") if hasattr(struct, 'get') else None
            if isinstance(k, pikepdf.Array):
                for child in list(k):
                    _walk(child)
            elif k is not None:
                _walk(k)
    except Exception:
        pass

    issues = []
    if missing_alt > 0:
        issues.append(WCAGIssue(
            criterion="1.1.1", level="A", title="Non-text Content",
            severity="error",
            description=f"{missing_alt} Figure element(s) have no /Alt or /ActualText entry.",
            fix="Add descriptive alt text to each Figure tag. For decorative images, "
                "mark them as Artifact instead.",
            location=f"{missing_alt} Figure elements",
        ))
    return issues


# ── 1.3.1 Info and Relationships — Form inputs need labels ────────────────────

def _check_form_labels(pdf) -> list[WCAGIssue]:
    import pikepdf

    unlabelled = 0
    try:
        acroform = pdf.Root.get("/AcroForm")
        if not acroform:
            return []
        acroform_obj = pdf.get_object(acroform.objgen) if hasattr(acroform, 'objgen') else acroform
        if not isinstance(acroform_obj, pikepdf.Dictionary):
            return []
        fields_ref = acroform_obj.get("/Fields")
        if not fields_ref:
            return []
        fields = pdf.get_object(fields_ref.objgen) if hasattr(fields_ref, 'objgen') else fields_ref
        if not isinstance(fields, pikepdf.Array):
            return []
        for field_ref in list(fields)[:100]:
            try:
                field = pdf.get_object(field_ref.objgen) if hasattr(field_ref, 'objgen') else field_ref
                if not isinstance(field, pikepdf.Dictionary):
                    continue
                tu = field.get("/TU")   # tooltip/accessible name
                t = field.get("/T")     # internal name
                if not tu and not t:
                    unlabelled += 1
            except Exception:
                continue
    except Exception:
        pass

    issues = []
    if unlabelled > 0:
        issues.append(WCAGIssue(
            criterion="1.3.1", level="A", title="Info and Relationships",
            severity="warning",
            description=f"{unlabelled} form field(s) lack an accessible name (/TU tooltip).",
            fix="Add /TU (tooltip/accessible name) to each AcroForm field so screen readers "
                "can announce the field's purpose.",
            location=f"{unlabelled} AcroForm fields",
        ))
    return issues


# ── 1.3.2 Meaningful Sequence (Reading Order) ────────────────────────────────

def _check_reading_order(pdf) -> list[WCAGIssue]:
    """Heuristic: detect struct elements in obvious reverse-Y order."""
    import pikepdf

    def _deref(obj):
        if obj is None:
            return None
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Array)):
            return obj
        if hasattr(obj, 'objgen') and obj.objgen != (0, 0):
            try:
                return pdf.get_object(obj.objgen)
            except Exception:
                pass
        return obj

    reversed_count = 0

    def _check_siblings(node, depth=0):
        nonlocal reversed_count
        if depth > 4:
            return
        try:
            obj = _deref(node)
            if not isinstance(obj, pikepdf.Dictionary):
                return
            k = obj.get("/K")
            if not isinstance(k, pikepdf.Array):
                return
            children = list(k)
            # Get MCIDs of first two children to check order
            mcids = []
            for child in children[:10]:
                if isinstance(child, int):
                    mcids.append(child)
                else:
                    c = _deref(child)
                    if isinstance(c, pikepdf.Dictionary):
                        ck = c.get("/K")
                        if isinstance(ck, int):
                            mcids.append(ck)
                        _check_siblings(child, depth + 1)
            # If MCIDs are strictly descending, that's suspicious
            if len(mcids) >= 3 and all(mcids[i] > mcids[i+1] for i in range(len(mcids)-1)):
                reversed_count += 1
        except Exception:
            pass

    try:
        struct = pdf.Root.get("/StructTreeRoot")
        if struct:
            k = _deref(struct).get("/K") if hasattr(struct, 'get') else None
            if isinstance(k, pikepdf.Array):
                for child in list(k):
                    _check_siblings(child)
            elif k is not None:
                _check_siblings(k)
    except Exception:
        pass

    issues = []
    if reversed_count > 0:
        issues.append(WCAGIssue(
            criterion="1.3.2", level="A", title="Meaningful Sequence",
            severity="warning",
            description=f"Detected {reversed_count} struct container(s) where children appear "
                        "in reverse MCID order, suggesting incorrect reading order.",
            fix="Ensure the tag tree K array order matches the logical top-to-bottom "
                "reading order. Recreate the PDF or use tag_in_place mode with reading "
                "order repair enabled.",
            location=f"{reversed_count} container(s)",
        ))
    return issues


# ── 1.3.1 Info and Relationships — Heading structure ────────────────────────

def _check_document_structure(pdf) -> list[WCAGIssue]:
    """Check that document has at least one H1 and a Document root tag."""
    import pikepdf

    def _deref(obj):
        if obj is None:
            return None
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Array)):
            return obj
        if hasattr(obj, 'objgen') and obj.objgen != (0, 0):
            try:
                return pdf.get_object(obj.objgen)
            except Exception:
                pass
        return obj

    has_h1 = False
    has_document_root = False
    issues = []

    try:
        struct = pdf.Root.get("/StructTreeRoot")
        if not struct:
            issues.append(WCAGIssue(
                criterion="1.3.1", level="A", title="Info and Relationships",
                severity="error",
                description="PDF has no structure tree (StructTreeRoot missing).",
                fix="Rebuild the PDF with proper tagging enabled. Use the 'rebuild' conversion mode.",
            ))
            return issues

        k = _deref(struct).get("/K") if hasattr(struct, 'get') else None
        if k is not None:
            first = None
            if isinstance(k, pikepdf.Array) and len(k) > 0:
                first = _deref(list(k)[0])
            elif not isinstance(k, pikepdf.Array):
                first = _deref(k)
            if first and isinstance(first, pikepdf.Dictionary):
                root_s = str(first.get("/S", ""))
                has_document_root = root_s == "/Document"

        def _find_h1(node, depth=0):
            nonlocal has_h1
            if depth > 30 or has_h1:
                return
            try:
                obj = _deref(node)
                if not isinstance(obj, pikepdf.Dictionary):
                    return
                if str(obj.get("/S", "")) == "/H1":
                    has_h1 = True
                    return
                ck = obj.get("/K")
                if isinstance(ck, pikepdf.Array):
                    for child in list(ck):
                        if not isinstance(child, int):
                            _find_h1(child, depth + 1)
                elif ck is not None and not isinstance(ck, int):
                    _find_h1(ck, depth + 1)
            except Exception:
                pass

        if isinstance(k, pikepdf.Array):
            for child in list(k):
                _find_h1(child)
        elif k is not None:
            _find_h1(k)

    except Exception:
        pass

    if not has_document_root:
        issues.append(WCAGIssue(
            criterion="1.3.1", level="A", title="Info and Relationships",
            severity="warning",
            description="Structure tree root element is not /Document type.",
            fix="Ensure the top-level tag is /Document. This is required by PDF/UA-1.",
        ))

    if not has_h1:
        issues.append(WCAGIssue(
            criterion="1.3.1", level="A", title="Info and Relationships",
            severity="info",
            description="Document contains no H1 heading. Most documents should have a primary heading.",
            fix="Add an H1 heading as the first structural heading in the document.",
        ))

    return issues
