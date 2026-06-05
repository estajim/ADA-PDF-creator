"""Build AccessibilityReport from veraPDF + PAC 2024 raw results + WCAG 2.1 AA checks."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ada_pdf.validation.verapdf import VeraPDFRawResult
from ada_pdf.validation.pac2024 import Pac2024Result, PacIssue


class AccessibilityIssue(BaseModel):
    rule_id: str
    severity: Literal["error", "warning", "info"]
    description: str
    location: str
    fix_suggestion: str


class WCAGAdvisory(BaseModel):
    """WCAG 2.1 AA advisory issue — does not affect the PDF/UA score."""
    criterion: str
    level: Literal["A", "AA", "AAA"]
    title: str
    severity: Literal["error", "warning", "info"]
    description: str
    fix: str
    location: str = ""


class AccessibilityReport(BaseModel):
    job_id: str
    validation_tool: str = "veraPDF"
    profile: str = "PDF/UA-1"
    passed: bool
    score: float | None         # None when validation could not run
    total_assertions: int
    passed_assertions: int
    failed_assertions: int
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    issues: list[AccessibilityIssue]
    wcag_advisories: list[WCAGAdvisory] = []
    parse_error: str = ""
    generated_at: str
    # PAC 2024 results (populated when PAC is installed)
    pac_available: bool = False
    pac_passed: bool = False
    pac_score: float | None = None
    pac_total_checks: int = 0
    pac_passed_checks: int = 0
    pac_failed_checks: int = 0
    pac_issues: list[AccessibilityIssue] = []

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    def to_html(self) -> str:
        return _render_html_report(self)


# Rule ID → (plain-English description, plain-English fix)
_RULE_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "ISO 14289-1:2014:6.2:1": (
        "PDF/UA identifier missing from XMP metadata",
        "The document needs a pdfuaid:part=1 entry in its XMP metadata. "
        "This signals to assistive technology that it is a PDF/UA-1 document.",
    ),
    "ISO 14289-1:2014:7.1:1": (
        "Document is not marked as tagged (MarkInfo/Marked is not True)",
        "Set MarkInfo/Marked=True in the PDF catalog. All content must be tagged.",
    ),
    "ISO 14289-1:2014:7.1:2": (
        "Untagged content found — some content has no corresponding tag",
        "Every piece of meaningful content must have a tag in the tag tree. "
        "Decorative elements should be marked as Artifact.",
    ),
    "ISO 14289-1:2014:7.1:3": (
        "Content block has an MCID that is not referenced in the structure tree",
        "The document has marked content (BDC/MCID) in the page stream that is "
        "not linked to any struct element. Add it to the ParentTree or mark it as Artifact.",
    ),
    "ISO 14289-1:2014:7.2:2": (
        "Document has no /Lang attribute — language is not declared",
        "Set the document language in the catalog: pdf.Root.Lang = 'en-US' "
        "(replace en-US with the actual language code).",
    ),
    "ISO 14289-1:2014:7.4.2:1": (
        "Heading hierarchy is wrong — levels are skipped (e.g. H1 → H3 with no H2)",
        "Headings must be sequential with no gaps. If H1 is the first heading, "
        "the next level must be H2, then H3, etc. Never jump from H1 to H3.",
    ),
    "ISO 14289-1:2014:7.5:1": (
        "Table header cell (TH) is missing a Scope attribute",
        "Each TH element needs a /Scope attribute: /Column for column headers, "
        "/Row for row headers. Add /A << /O /Table /Scope /Column >> to the TH tag.",
    ),
    "ISO 14289-1:2014:7.3:1": (
        "Figure element is missing alternate text",
        "Add an /Alt entry to every Figure tag describing the image content. "
        "Decorative images should be marked as Artifact, not Figure.",
    ),
    "ISO 14289-1:2014:7.9:1": (
        "Note element is missing a required /ID attribute",
        "Every /Note struct element must have a unique /ID string so it can be "
        "referenced by assistive technology.",
    ),
    "ISO 14289-1:2014:7.21.4.1": (
        "Font is not embedded in the PDF",
        "All fonts used for text must be embedded. Re-export the document with "
        "font embedding enabled. This is a hard requirement of PDF/UA.",
    ),
    "ISO 14289-1:2014:7.21.4.2": (
        "Font's Unicode character map (ToUnicode / ActualText) is incomplete",
        "The font used does not provide a complete character-to-Unicode mapping, "
        "so screen readers cannot read the text correctly. Re-export with proper "
        "Unicode font encoding.",
    ),
    "ISO 14289-1:2014:7.1:7": (
        "RoleMap remaps a standard PDF structure type to another standard type",
        "Standard tags like /P, /H1, /Table must not be remapped in the RoleMap. "
        "Remove any RoleMap entries that map standard tags to other standard tags.",
    ),
    "ISO 14289-1:2014:7.18.1:2": (
        "Link annotation is missing a Contents or Alt entry (accessible name)",
        "Every hyperlink annotation must have a /Contents string describing its "
        "destination. Screen readers use this to announce the link purpose.",
    ),
    "ISO 14289-1:2014:7.18.3:1": (
        "Annotated page is missing Tabs=S (tab order not set to structure order)",
        "Pages with annotations must have /Tabs /S in their page dictionary so "
        "keyboard navigation follows the reading order.",
    ),
    "ISO 14289-1:2014:7.10:1": (
        "Optional content (layers) configuration is missing or incomplete",
        "If the PDF uses optional content groups (layers), the /OCProperties "
        "must include a /D (default configuration) entry.",
    ),
    "ISO 14289-1:2014:7.11:1": (
        "Embedded file attachment is missing a description (F or UF key)",
        "File attachments must have accessible names. Set the /F and /UF entries "
        "in the embedded file dictionary to descriptive filenames.",
    ),
    "ISO 14289-1:2014:7.2:3": (
        "Language cannot be determined for some text content",
        "Text in a language different from the document default must be tagged "
        "with a /Lang attribute at the span or block level.",
    ),
}


_PAC_FIX_HINT: dict[str, str] = {
    "7.3": "Add /Alt text to every Figure tag. Decorative images must be marked Artifact.",
    "7.1": "Set MarkInfo/Marked=True in the PDF catalog and tag all content.",
    "6.2": "Add pdfuaid:part=1 to the XMP metadata stream.",
    "7.2": "Set /Lang on the document catalog to the primary language code (e.g. en-US).",
    "7.4.2": "Fix heading hierarchy — headings must not skip levels (H1 → H2 → H3).",
    "7.5": "Add /Scope (Column or Row) to every TH table-header element.",
    "7.18.1": "Add /Contents (accessible name) to every link annotation.",
    "7.18.3": "Add /Tabs /S to every page dictionary that has annotations.",
    "7.21.4": "Embed all fonts used in the document.",
    "7.21.7": "Provide complete Unicode character mapping (ToUnicode or ActualText).",
}


def build(job_id: str, raw: VeraPDFRawResult,
          output_path: Path | None = None,
          pac_result: Pac2024Result | None = None) -> AccessibilityReport:
    """Build a full report from veraPDF results and optional WCAG checks."""
    passed_count = raw.passed_rules
    failed_count = raw.failed_rules
    total = passed_count + failed_count

    if raw.parse_error:
        score = None
    elif total > 0:
        score = round(passed_count / total, 4)
    else:
        score = 1.0 if raw.compliant else None

    issues: list[AccessibilityIssue] = []
    for assertion in raw.assertions:
        if assertion.status != "FAILED":
            continue
        desc, fix = _RULE_DESCRIPTIONS.get(
            assertion.rule_id,
            (assertion.message, "Review the PDF/UA-1 specification for this rule."),
        )
        issues.append(AccessibilityIssue(
            rule_id=assertion.rule_id,
            severity="error",
            description=desc or assertion.message,
            location=assertion.location,
            fix_suggestion=fix,
        ))

    # Run WCAG 2.1 AA advisory checks if output file is available
    wcag_advisories: list[WCAGAdvisory] = []
    if output_path and output_path.exists():
        try:
            from ada_pdf.validation.wcag_checker import run_wcag_checks, WCAGIssue
            for wi in run_wcag_checks(output_path):
                wcag_advisories.append(WCAGAdvisory(
                    criterion=wi.criterion,
                    level=wi.level,
                    title=wi.title,
                    severity=wi.severity,
                    description=wi.description,
                    fix=wi.fix,
                    location=wi.location,
                ))
        except Exception:
            pass

    # Build PAC 2024 section
    pac_issues: list[AccessibilityIssue] = []
    if pac_result and pac_result.available:
        for pi in pac_result.issues:
            pac_issues.append(AccessibilityIssue(
                rule_id=pi.rule_id,
                severity="error",
                description=pi.description,
                location=pi.location,
                fix_suggestion=_PAC_FIX_HINT.get(pi.rule_id, "Review PAC 2024 report for remediation guidance."),
            ))

    return AccessibilityReport(
        job_id=job_id,
        passed=raw.compliant,
        score=score,
        total_assertions=total,
        passed_assertions=passed_count,
        failed_assertions=failed_count,
        total_checks=raw.passed_checks + raw.failed_checks,
        passed_checks=raw.passed_checks,
        failed_checks=raw.failed_checks,
        issues=issues,
        wcag_advisories=wcag_advisories,
        parse_error=raw.parse_error,
        generated_at=datetime.now(timezone.utc).isoformat(),
        profile=raw.profile_name,
        pac_available=bool(pac_result and pac_result.available),
        pac_passed=bool(pac_result and pac_result.compliant),
        pac_score=pac_result.score if pac_result and pac_result.available else None,
        pac_total_checks=pac_result.total_checks if pac_result else 0,
        pac_passed_checks=pac_result.passed_checks if pac_result else 0,
        pac_failed_checks=pac_result.failed_checks if pac_result else 0,
        pac_issues=pac_issues,
    )


def _render_html_report(report: AccessibilityReport) -> str:
    status_color = "#2e7d32" if report.passed else "#c62828"
    status_text = "PASS" if report.passed else "FAIL"
    pct = round((report.score or 0.0) * 100, 1)

    # PDF/UA issues table
    ua_rows = ""
    for issue in report.issues:
        sev_color = "#c62828" if issue.severity == "error" else "#e65100"
        ua_rows += f"""
<tr>
  <td style="color:{sev_color};font-weight:bold">{issue.severity.upper()}</td>
  <td><code>{issue.rule_id}</code></td>
  <td>{issue.description}</td>
  <td style="font-size:8.5pt;color:#555">{issue.location or '—'}</td>
  <td style="color:#1565c0">{issue.fix_suggestion}</td>
</tr>"""

    ua_section = ""
    if report.issues:
        ua_section = f"""
<h2>PDF/UA-1 Issues ({len(report.issues)})</h2>
<table>
<thead><tr>
  <th>Severity</th><th>Rule ID</th><th>What's Wrong</th>
  <th>Location</th><th>How to Fix It</th>
</tr></thead>
<tbody>{ua_rows}</tbody>
</table>"""
    else:
        ua_section = "<p class='ok'>✓ No PDF/UA-1 issues found. Document is structurally compliant.</p>"

    # WCAG advisories table
    wcag_rows = ""
    for adv in report.wcag_advisories:
        sev_color = {"error": "#c62828", "warning": "#e65100", "info": "#1565c0"}.get(adv.severity, "#333")
        wcag_rows += f"""
<tr>
  <td style="color:{sev_color};font-weight:bold">{adv.severity.upper()}</td>
  <td><code>WCAG {adv.criterion}</code><br/><small>{adv.level}</small></td>
  <td><strong>{adv.title}</strong><br/>{adv.description}</td>
  <td style="font-size:8.5pt;color:#555">{adv.location or '—'}</td>
  <td style="color:#1565c0">{adv.fix}</td>
</tr>"""

    wcag_section = ""
    if report.wcag_advisories:
        wcag_section = f"""
<h2>WCAG 2.1 AA Advisory Checks ({len(report.wcag_advisories)})</h2>
<p style="color:#555;font-size:9pt">These checks go beyond PDF/UA-1 and do not affect your compliance score,
but should be addressed for full ADA/WCAG 2.1 AA conformance.</p>
<table>
<thead><tr>
  <th>Severity</th><th>Criterion</th><th>What Was Found</th>
  <th>Location</th><th>How to Fix It</th>
</tr></thead>
<tbody>{wcag_rows}</tbody>
</table>"""
    elif not report.parse_error:
        wcag_section = "<p class='ok'>✓ No WCAG 2.1 AA advisory issues found.</p>"

    # Remediation checklist — plain English
    checklist_items = ""
    all_issues = [(i.rule_id, i.fix_suggestion) for i in report.issues]
    for adv in report.wcag_advisories:
        if adv.severity == "error":
            all_issues.append((f"WCAG {adv.criterion}", adv.fix))
    if all_issues:
        unique = {}
        for rid, fix in all_issues:
            if fix not in unique:
                unique[fix] = rid
        for fix, rid in unique.items():
            checklist_items += f'<li><label><input type="checkbox"/> [{rid}] {fix}</label></li>\n'

    checklist_section = ""
    if checklist_items:
        checklist_section = f"""
<h2>Remediation Checklist</h2>
<p style="color:#555;font-size:9pt">Check off each item as you fix it:</p>
<ul style="line-height:1.8">{checklist_items}</ul>"""

    parse_err_html = (
        f"<p style='color:#c62828'>⚠ Validation parse error: {report.parse_error}</p>"
        if report.parse_error else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <title>Accessibility Report — Job {report.job_id[:8]}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 2cm; font-size: 10pt; color: #222; }}
    h1 {{ font-size: 1.5em; border-bottom: 2px solid #2c3e50; padding-bottom: 0.3em; }}
    h2 {{ font-size: 1.1em; margin-top: 1.5em; color: #2c3e50; }}
    .badge {{ display:inline-block; padding:0.3em 0.9em; border-radius:4px;
              color:#fff; background:{status_color}; font-weight:bold; font-size:1.1em; }}
    .summary {{ display:flex; gap:1.5em; margin:1em 0; flex-wrap:wrap; }}
    .metric {{ background:#f5f5f5; padding:0.8em 1.2em; border-radius:6px; min-width:100px; }}
    .metric span {{ display:block; font-size:1.8em; font-weight:bold; color:#2c3e50; }}
    .ok {{ color:#2e7d32; }}
    table {{ border-collapse:collapse; width:100%; margin-top:0.5em; font-size:9pt; }}
    th {{ background:#ecf0f1; padding:0.5em 0.6em; text-align:left;
          border:1px solid #bdc3c7; font-size:9pt; }}
    td {{ padding:0.4em 0.6em; border:1px solid #ddd; vertical-align:top; }}
    tr:nth-child(even) td {{ background:#fafafa; }}
    ul {{ padding-left:1.5em; }}
    code {{ background:#f4f4f4; padding:1px 4px; border-radius:3px; font-size:8.5pt; }}
    small {{ color:#888; }}
  </style>
</head>
<body>
<h1>PDF Accessibility Validation Report</h1>
<p><strong>Job:</strong> <code>{report.job_id}</code></p>
<p><strong>Profile:</strong> {report.profile} + WCAG 2.1 AA Advisory</p>
<p><strong>Overall Status:</strong> <span class="badge">{status_text}</span>
   &nbsp; <strong>PDF/UA-1 Score:</strong> {pct}%</p>

<div class="summary">
  <div class="metric"><span>{pct}%</span>PDF/UA Score</div>
  <div class="metric"><span>{report.passed_assertions}</span>Rules Passed</div>
  <div class="metric"><span>{report.failed_assertions}</span>Rules Failed</div>
  <div class="metric"><span>{len(report.wcag_advisories)}</span>WCAG Advisories</div>
  <div class="metric"><span>{report.total_assertions}</span>Total Rules</div>
</div>

{parse_err_html}
{ua_section}
{wcag_section}
{checklist_section}

<hr/>
<p style="color:#aaa;font-size:8pt">
  Generated: {report.generated_at} · ADA-PDF-Creator v1.0 ·
  PDF/UA-1 validation by veraPDF · WCAG checks by built-in checker
</p>
</body>
</html>"""
