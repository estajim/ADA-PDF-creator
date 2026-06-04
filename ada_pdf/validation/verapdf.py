"""veraPDF subprocess wrapper for PDF/UA-1 validation."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ada_pdf.utils.logging import get_logger

logger = get_logger(__name__)

_VERAPDF_TIMEOUT = 120  # seconds


@dataclass
class RuleAssertion:
    rule_id: str      # e.g. "ISO 14289-1:2014:7.2"
    status: str       # "FAILED" | "PASSED"
    message: str      # human-readable description of the rule
    location: str     # XPath context of first failing check


@dataclass
class VeraPDFRawResult:
    compliant: bool
    profile_name: str
    assertions: list[RuleAssertion]
    passed_rules: int = 0
    failed_rules: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    parse_error: str = ""


class VeraPDFError(Exception):
    pass


def validate(pdf_path: Path, cli_path: str) -> VeraPDFRawResult:
    """Run veraPDF against pdf_path and return structured results.

    Output goes to stdout as JSON — the CLI wrapper class does not use --reportfile.
    Exit codes: 0 = compliant, 1 = non-compliant, 2+ = tool error.
    """
    if not Path(cli_path).exists():
        logger.warning("verapdf_not_found", cli=cli_path)
        return VeraPDFRawResult(
            compliant=False,
            profile_name="PDF/UA-1",
            assertions=[],
            parse_error=f"veraPDF not found at {cli_path}",
        )

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            result = subprocess.run(
                [cli_path, "-f", "ua1", "--format", "json", str(pdf_path)],
                capture_output=True,
                text=True,
                timeout=_VERAPDF_TIMEOUT,
            )
            break
        except subprocess.TimeoutExpired:
            raise VeraPDFError("veraPDF timed out")
        except Exception as exc:
            last_exc = exc
            logger.warning("verapdf_attempt_failed", attempt=attempt + 1, error=str(exc))
    else:
        raise VeraPDFError(f"veraPDF subprocess error after 3 attempts: {last_exc}") from last_exc

    if result.returncode > 1:
        raise VeraPDFError(
            f"veraPDF exited with code {result.returncode}: {result.stderr[:500]}"
        )

    stdout = result.stdout.strip()
    if not stdout:
        raise VeraPDFError("veraPDF produced no output")

    try:
        raw = json.loads(stdout)
        return _parse_verapdf_json(raw)
    except (json.JSONDecodeError, KeyError) as exc:
        return VeraPDFRawResult(
            compliant=False,
            profile_name="PDF/UA-1",
            assertions=[],
            parse_error=f"Could not parse veraPDF output: {exc}",
        )


def _parse_verapdf_json(raw: dict[str, Any]) -> VeraPDFRawResult:
    """Parse veraPDF 1.26 JSON output.

    Structure: raw["report"]["jobs"][0]["validationResult"]
    Rules are in validationResult["details"]["ruleSummaries"].
    """
    try:
        report = raw.get("report", raw)              # handle both {report:{...}} and direct
        job = report["jobs"][0]
        vr = job.get("validationResult", {})
        details = vr.get("details", {})

        passed_rules  = details.get("passedRules", 0)
        failed_rules  = details.get("failedRules", 0)
        passed_checks = details.get("passedChecks", 0)
        failed_checks = details.get("failedChecks", 0)
        profile_name  = vr.get("profileName", "PDF/UA-1")
        # compliant: either explicit field or derived from failedRules
        compliant = bool(vr.get("compliant", failed_rules == 0 and bool(vr)))

        assertions: list[RuleAssertion] = []
        for rule in details.get("ruleSummaries", []):
            rule_status = rule.get("ruleStatus", rule.get("status", "PASSED")).upper()
            spec    = rule.get("specification", "")
            clause  = rule.get("clause", "")
            testnum = rule.get("testNumber", "")
            rule_id = f"{spec}:{clause}" + (f":{testnum}" if testnum else "")
            description = rule.get("description", "")

            # First failing check location
            first_fail_ctx = ""
            for chk in rule.get("checks", []):
                if chk.get("status") == "failed":
                    first_fail_ctx = chk.get("context", "")
                    if not description:
                        description = chk.get("errorMessage", "")
                    break

            assertions.append(RuleAssertion(
                rule_id=rule_id,
                status=rule_status,
                message=description,
                location=first_fail_ctx,
            ))

        return VeraPDFRawResult(
            compliant=compliant,
            profile_name=profile_name,
            assertions=assertions,
            passed_rules=passed_rules,
            failed_rules=failed_rules,
            passed_checks=passed_checks,
            failed_checks=failed_checks,
        )
    except (KeyError, IndexError) as exc:
        return VeraPDFRawResult(
            compliant=False,
            profile_name="PDF/UA-1",
            assertions=[],
            parse_error=f"Unexpected veraPDF JSON structure: {exc}",
        )
