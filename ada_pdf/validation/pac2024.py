"""PAC 2024 subprocess wrapper for PDF/UA-1 + WCAG 2.x validation.

PAC 2024 by the PDF/UA Foundation is the most thorough free checker.
On Mac it ships as /Applications/PAC 2024.app — point PAC_2024_CLI_PATH at
the binary inside the bundle (or set it to the Windows .exe path on Windows).

If PAC is not installed, every call returns a Pac2024Result with
available=False and the rest of the fields empty/zero.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ada_pdf.utils.logging import get_logger

logger = get_logger(__name__)

_PAC_TIMEOUT = 120  # seconds

# Common install paths — first found wins
_DEFAULT_PATHS_MAC = [
    "/Applications/PAC 2024.app/Contents/MacOS/PAC 2024",
    "/Applications/PAC2024.app/Contents/MacOS/PAC2024",
]
_DEFAULT_PATHS_WIN = [
    r"C:\Program Files\PAC 2024\PAC2024.exe",
]


@dataclass
class PacIssue:
    rule_id: str       # e.g. "PDF/UA-1 7.3" or WCAG criterion
    category: str      # "PDF/UA-1" | "WCAG 2.x" | "PDF/UA-2"
    description: str
    location: str = ""


@dataclass
class Pac2024Result:
    available: bool
    compliant: bool = False
    score: float | None = None       # passed_checks / total_checks
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    issues: list[PacIssue] = field(default_factory=list)
    error: str = ""


def validate(pdf_path: Path, cli_path: str) -> Pac2024Result:
    """Run PAC 2024 against pdf_path and return structured results.

    PAC 2024 CLI on Mac (inside the .app bundle) accepts:
        PAC\ 2024 <input.pdf> [--export <report.json>]

    PAC returns:
        exit 0  – document is compliant
        exit 1  – document is non-compliant or errors found
        exit 2+ – tool error
    """
    resolved = _resolve_cli(cli_path)
    if not resolved:
        logger.info("pac2024_not_found", searched=cli_path)
        return Pac2024Result(available=False, error="PAC 2024 not installed")

    with tempfile.TemporaryDirectory() as tmp:
        report_file = Path(tmp) / "pac_report.json"
        cmd = [resolved, str(pdf_path), "--export", str(report_file)]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_PAC_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return Pac2024Result(available=True, error="PAC 2024 timed out")
        except Exception as exc:
            return Pac2024Result(available=True, error=f"PAC 2024 subprocess error: {exc}")

        # Try JSON report first; fall back to parsing stdout
        if report_file.exists():
            try:
                return _parse_json_report(report_file.read_text(encoding="utf-8", errors="replace"))
            except Exception as exc:
                logger.warning("pac2024_json_parse_failed", error=str(exc))

        return _parse_stdout(proc.stdout + proc.stderr, proc.returncode)


def _resolve_cli(cli_path: str) -> str | None:
    """Return the first usable PAC binary path, or None."""
    candidates = [cli_path] if cli_path else []
    import platform
    if platform.system() == "Darwin":
        candidates += _DEFAULT_PATHS_MAC
    else:
        candidates += _DEFAULT_PATHS_WIN
    for p in candidates:
        if p and Path(p).exists():
            return p
    return None


def _parse_json_report(json_text: str) -> Pac2024Result:
    """Parse PAC 2024 JSON export format."""
    data = json.loads(json_text)

    # PAC 2024 JSON structure (observed in v2024):
    # { "result": "pass"|"fail", "checks": [...] }
    issues: list[PacIssue] = []
    checks = data.get("checks", data.get("errors", []))
    total = len(checks)
    failed = 0
    for chk in checks:
        status = str(chk.get("status", chk.get("result", "pass"))).lower()
        if status in ("fail", "failed", "error"):
            failed += 1
            rule_id = chk.get("ruleId", chk.get("rule", chk.get("id", "unknown")))
            category = chk.get("category", "PDF/UA-1")
            description = chk.get("description", chk.get("message", ""))
            location = chk.get("location", chk.get("context", ""))
            issues.append(PacIssue(
                rule_id=str(rule_id),
                category=str(category),
                description=str(description),
                location=str(location),
            ))

    passed = total - failed
    score = round(passed / total, 4) if total > 0 else (1.0 if not issues else 0.0)
    compliant_str = str(data.get("result", data.get("compliant", "fail"))).lower()
    compliant = compliant_str in ("pass", "passed", "true", "1", "compliant")

    return Pac2024Result(
        available=True,
        compliant=compliant,
        score=score,
        total_checks=total,
        passed_checks=passed,
        failed_checks=failed,
        issues=issues,
    )


def _parse_stdout(text: str, returncode: int) -> Pac2024Result:
    """Fallback: parse PAC plain-text output."""
    if not text.strip():
        return Pac2024Result(
            available=True,
            compliant=(returncode == 0),
            error="PAC 2024 produced no output" if returncode != 0 else "",
        )

    issues: list[PacIssue] = []
    # Simple heuristic: lines starting with "FAIL" or "Error"
    fail_pattern = re.compile(r"(FAIL|ERROR|FAILED)[:\s]+(.+)", re.IGNORECASE)
    for line in text.splitlines():
        m = fail_pattern.search(line)
        if m:
            issues.append(PacIssue(
                rule_id="unknown",
                category="PDF/UA-1",
                description=m.group(2).strip(),
            ))

    compliant = returncode == 0 and not issues
    total = max(1, len(issues) * 2)  # rough estimate
    failed = len(issues)
    score = round((total - failed) / total, 4) if total > 0 else None

    return Pac2024Result(
        available=True,
        compliant=compliant,
        score=score,
        total_checks=total,
        passed_checks=total - failed,
        failed_checks=failed,
        issues=issues,
    )
