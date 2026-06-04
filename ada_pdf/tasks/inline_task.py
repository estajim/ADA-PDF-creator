"""SIMPLE_MODE task: run the accessibility pipeline in a thread pool.

Replaces Celery's process_document task when SIMPLE_MODE=true.
Called via FastAPI BackgroundTasks — no Redis or Celery broker needed.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ada_pdf.config import get_settings
from ada_pdf.models.orm import Job, JobStatus, ValidationResult
from ada_pdf.pipeline.pipeline import AccessibilityPipeline
from ada_pdf.pipeline.stages.s1_ingest import IngestError
from ada_pdf.pipeline.stages.s3_ocr import OCRError
from ada_pdf.pipeline.stages.s6_alttext import AltTextError
from ada_pdf.pipeline.stages.s7_generate import GenerationError
from ada_pdf.storage.local import LocalStorage
from ada_pdf.utils.logging import get_logger
from ada_pdf.validation import report_builder, verapdf, pac2024

logger = get_logger(__name__)


async def process_inline(job_id: str, options: dict | None = None) -> None:
    """Async entry-point called by FastAPI BackgroundTasks.

    Offloads the CPU-heavy pipeline to a thread so the event loop stays free.
    """
    await asyncio.to_thread(_run_sync, job_id, options or {})


# ── Synchronous pipeline runner (executed in a thread) ─────────────────────

def _run_sync(job_id: str, options: dict) -> None:
    """Identical logic to process_document.py but without Celery decoration."""
    settings = get_settings()
    storage = LocalStorage(settings.STORAGE_ROOT)
    session = _sync_session(settings)

    logger.info("inline_task_start", job_id=job_id)
    _update_job(session, job_id, status=JobStatus.PROCESSING)

    def _progress(status: str, pct: int) -> None:
        _update_job(session, job_id, progress_status=status, progress_pct=pct)

    try:
        job = session.get(Job, uuid.UUID(job_id))
        if job is None:
            raise RuntimeError(f"Job {job_id} not found")

        input_path = Path(job.input_path)
        job_options = options or (json.loads(job.options_json) if job.options_json else {})

        pipeline = AccessibilityPipeline(settings)
        result = pipeline.run(
            input_path, job_id,
            progress_callback=_progress,
            options=job_options,
        )

        if result.output_path and result.output_path.exists():
            pdf_bytes = result.output_path.read_bytes()
            out_path = storage.save_output(job_id, pdf_bytes)
        else:
            out_path = result.output_path

        # Validation
        validation_passed = ValidationResult.SKIPPED
        report_path = None
        try:
            raw_result = verapdf.validate(out_path, settings.VERAPDF_CLI_PATH)
            pac_result = None
            try:
                pac_result = pac2024.validate(out_path, settings.PAC_2024_CLI_PATH)
            except Exception:
                pass
            report = report_builder.build(job_id, raw_result, output_path=out_path, pac_result=pac_result)
            report_bytes = report.to_json().encode()
            report_path = storage.save_report(job_id, report_bytes)
            validation_passed = ValidationResult.PASS if report.passed else ValidationResult.FAIL
        except verapdf.VeraPDFError as exc:
            logger.warning("validation_skipped", error=str(exc))

        final_status = (
            JobStatus.COMPLETED
            if validation_passed in (ValidationResult.PASS, ValidationResult.SKIPPED)
            else JobStatus.PARTIAL
        )
        _update_job(
            session, job_id,
            status=final_status,
            output_path=str(out_path),
            report_path=str(report_path) if report_path else None,
            page_count=result.page_count,
            processing_time_seconds=round(result.processing_time, 2),
            validation_result=validation_passed,
            progress_pct=100,
            progress_status="completed",
        )
        logger.info("inline_task_done", job_id=job_id, status=final_status)

    except (IngestError, OCRError, GenerationError) as exc:
        logger.error("inline_task_failed", job_id=job_id, error=str(exc))
        _update_job(session, job_id, status=JobStatus.FAILED,
                    error_message=str(exc), progress_status="failed")
    except Exception as exc:
        logger.exception("inline_task_unexpected", job_id=job_id, error=str(exc))
        _update_job(session, job_id, status=JobStatus.FAILED,
                    error_message=f"Unexpected error: {str(exc)[:500]}",
                    progress_status="failed")
    finally:
        session.close()


# ── DB helpers ─────────────────────────────────────────────────────────────

def _sync_session(settings):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    url = settings.DATABASE_URL
    # Convert async URL to sync driver
    sync_url = (
        url.replace("+asyncpg", "+psycopg2")
           .replace("+aiosqlite", "")
    )
    kwargs: dict = {}
    if sync_url.startswith("sqlite"):
        from sqlalchemy.pool import StaticPool
        kwargs = {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }
    engine = create_engine(sync_url, **kwargs)
    return sessionmaker(engine)()


def _update_job(session, job_id: str, **kwargs) -> None:
    from sqlalchemy import update
    kwargs["updated_at"] = datetime.now(timezone.utc)
    session.execute(
        update(Job).where(Job.id == uuid.UUID(job_id)).values(**kwargs)
    )
    session.commit()
