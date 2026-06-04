"""Regression suite: every PDF in tests/sample_pdf/processed/ must reach 100% veraPDF.

Run with:
    E2E_ENABLED=1 pytest tests/e2e/test_regression_processed.py -v

Each PDF is converted via the full pipeline and validated with veraPDF. A score
below 1.0 (100%) fails the test, printing the failing rule IDs for diagnosis.

This suite is the safety net that catches regressions when pipeline code changes.
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

from ada_pdf.config import get_settings
from ada_pdf.pipeline.pipeline import AccessibilityPipeline
from ada_pdf.validation import report_builder, verapdf

PROCESSED = Path(__file__).parent.parent / "sample_pdf" / "processed"

pytestmark = pytest.mark.e2e


def _is_e2e() -> bool:
    return os.environ.get("E2E_ENABLED", "0") == "1"


@pytest.fixture(autouse=True)
def require_e2e():
    if not _is_e2e():
        pytest.skip("E2E_ENABLED=1 required")


@pytest.fixture(scope="module")
def pipeline():
    return AccessibilityPipeline(get_settings())


@pytest.fixture
def isolated_pdf(tmp_path):
    """Copy a PDF into tmp_path so pipeline outputs don't pollute the source tree."""
    def _copy(src: Path) -> Path:
        dest = tmp_path / src.name
        shutil.copy2(src, dest)
        return dest
    return _copy


def _processed_pdfs() -> list[Path]:
    if not PROCESSED.exists():
        return []
    return sorted(PROCESSED.glob("*.pdf"))


@pytest.mark.parametrize("pdf_path", _processed_pdfs(), ids=lambda p: p.name)
def test_100_percent_compliance(pipeline, isolated_pdf, pdf_path: Path):
    """Every processed PDF must convert to a 100%-passing PDF/UA-1 document."""
    job_id = str(uuid.uuid4())

    result = pipeline.run(
        isolated_pdf(pdf_path),
        job_id,
        options={"quality": "thorough", "conversion_mode": "rebuild",
                 "generate_alt_text": True, "detect_forms": True},
    )

    assert result.output_path is not None, f"Pipeline produced no output for {pdf_path.name}"
    assert result.output_path.exists(), f"Output file missing for {pdf_path.name}"

    settings = get_settings()
    raw = verapdf.validate(result.output_path, settings.VERAPDF_CLI_PATH)
    report = report_builder.build(job_id, raw)

    failing = [i.rule_id for i in report.issues] if report.issues else []
    assert report.score == 1.0, (
        f"{pdf_path.name}: veraPDF score {report.score:.2%} — failing rules: {failing}"
    )


@pytest.mark.parametrize("pdf_path", _processed_pdfs(), ids=lambda p: p.name)
def test_tag_in_place_compliance(pipeline, isolated_pdf, pdf_path: Path):
    """PDFs must also reach ≥90% in tag_in_place mode (less aggressive target)."""
    job_id = str(uuid.uuid4())

    result = pipeline.run(
        isolated_pdf(pdf_path),
        job_id,
        options={"quality": "thorough", "conversion_mode": "tag_in_place",
                 "generate_alt_text": True, "detect_forms": True},
    )

    assert result.output_path is not None
    settings = get_settings()
    raw = verapdf.validate(result.output_path, settings.VERAPDF_CLI_PATH)
    report = report_builder.build(job_id, raw)

    failing = [i.rule_id for i in report.issues] if report.issues else []
    assert report.score >= 0.90, (
        f"{pdf_path.name} [tag_in_place]: score {report.score:.2%} — failing: {failing}"
    )
