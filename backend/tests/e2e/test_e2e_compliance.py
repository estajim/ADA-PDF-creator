"""End-to-end compliance tests: real PDF → pipeline → veraPDF.

Run with: E2E_ENABLED=1 pytest tests/e2e -v
Requires Docker services (postgres, redis) and veraPDF installed.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from ada_pdf.config import get_settings
from ada_pdf.pipeline.pipeline import AccessibilityPipeline
from ada_pdf.validation import report_builder, verapdf

FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.e2e


def _is_e2e() -> bool:
    return os.environ.get("E2E_ENABLED", "0") == "1"


@pytest.fixture(autouse=True)
def require_e2e():
    if not _is_e2e():
        pytest.skip("E2E_ENABLED=1 required")


@pytest.fixture
def pipeline():
    return AccessibilityPipeline(get_settings())


@pytest.fixture
def job_id():
    return str(uuid.uuid4())


def _run_and_validate(pipeline, fixture_name: str, job_id: str) -> float:
    """Run pipeline on fixture PDF and return veraPDF compliance score."""
    pdf_path = FIXTURES / fixture_name
    if not pdf_path.exists():
        pytest.skip(f"Fixture {fixture_name} not present")

    result = pipeline.run(pdf_path, job_id)
    assert result.output_path is not None, "Pipeline produced no output"
    assert result.output_path.exists(), "Output PDF not written to disk"

    settings = get_settings()
    raw = verapdf.validate(result.output_path, settings.VERAPDF_CLI_PATH)
    report = report_builder.build(job_id, raw)
    return report.score


@pytest.mark.parametrize("fixture", [
    "digital_sample.pdf",
    "scanned_sample.pdf",
    "mixed_sample.pdf",
    "multicolumn_sample.pdf",
    "table_heavy_sample.pdf",
])
def test_compliance_score(pipeline, job_id, fixture):
    score = _run_and_validate(pipeline, fixture, job_id)
    assert score >= 0.90, f"Compliance score {score:.2%} below 90% threshold for {fixture}"
