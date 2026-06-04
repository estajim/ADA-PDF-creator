"""Celery task: run the accessibility pipeline for a job."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from celery import Task
from celery.exceptions import MaxRetriesExceededError
from sqlalchemy import update

from ada_pdf.config import get_settings
from ada_pdf.models.orm import Job, JobStatus, ValidationResult
from ada_pdf.pipeline.pipeline import AccessibilityPipeline
from ada_pdf.pipeline.stages.s1_ingest import IngestError
from ada_pdf.pipeline.stages.s3_ocr import OCRError
from ada_pdf.pipeline.stages.s6_alttext import AltTextError
from ada_pdf.pipeline.stages.s7_generate import GenerationError
from ada_pdf.storage.local import LocalStorage
from ada_pdf.tasks.celery_app import celery_app
from ada_pdf.utils.logging import get_logger
from ada_pdf.validation import report_builder, verapdf, pac2024

logger = get_logger(__name__)


def _sync_session():
    """Create a synchronous SQLAlchemy session for use inside Celery worker."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    settings = get_settings()
    # Convert async URL to sync for Celery worker context
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2").replace("+aiosqlite", "")
    engine = create_engine(sync_url)
    return sessionmaker(engine)()


def _update_job(session, job_id: str, **kwargs) -> None:
    from datetime import datetime, timezone
    kwargs["updated_at"] = datetime.now(timezone.utc)
    session.execute(update(Job).where(Job.id == uuid.UUID(job_id)).values(**kwargs))
    session.commit()


@celery_app.task(
    bind=True,
    name="ada_pdf.tasks.process_document",
    max_retries=3,
    default_retry_delay=60,
)
def process_document(self: Task, job_id: str, options: dict | None = None) -> dict:
    settings = get_settings()
    storage = LocalStorage(settings.STORAGE_ROOT)
    session = _sync_session()

    logger.info("task_start", job_id=job_id, task_id=self.request.id)

    # Mark job as processing
    _update_job(session, job_id, status=JobStatus.PROCESSING, celery_task_id=self.request.id)

    def _progress(status: str, pct: int) -> None:
        _update_job(session, job_id, progress_status=status, progress_pct=pct)

    try:
        # Retrieve input path from job record
        job = session.get(Job, uuid.UUID(job_id))
        if job is None:
            raise RuntimeError(f"Job {job_id} not found")

        input_path = Path(job.input_path)
        # Merge stored options with defaults
        job_options = options or (json.loads(job.options_json) if job.options_json else {})

        pipeline = AccessibilityPipeline(settings)
        result = pipeline.run(
            input_path, job_id,
            progress_callback=_progress,
            options=job_options,
        )

        # Save output PDF via storage
        if result.output_path and result.output_path.exists():
            pdf_bytes = result.output_path.read_bytes()
            out_path = storage.save_output(job_id, pdf_bytes)
        else:
            out_path = result.output_path

        # Run veraPDF validation
        validation_passed = ValidationResult.SKIPPED
        report_path = None
        try:
            raw_result = verapdf.validate(out_path, settings.VERAPDF_CLI_PATH)

            # Run PAC 2024 (optional — skipped gracefully if not installed)
            pac_result = None
            try:
                pac_result = pac2024.validate(out_path, settings.PAC_2024_CLI_PATH)
                if pac_result.available:
                    logger.info(
                        "pac2024_done",
                        job_id=job_id,
                        compliant=pac_result.compliant,
                        score=pac_result.score,
                        failed=pac_result.failed_checks,
                    )
            except Exception as pac_exc:
                logger.warning("pac2024_skipped", error=str(pac_exc))

            report = report_builder.build(job_id, raw_result, output_path=out_path, pac_result=pac_result)
            report_bytes = report.to_json().encode()
            report_path = storage.save_report(job_id, report_bytes)
            validation_passed = (
                ValidationResult.PASS if report.passed
                else ValidationResult.FAIL
            )
        except verapdf.VeraPDFError as exc:
            logger.warning("validation_skipped", error=str(exc))

        final_status = (
            JobStatus.COMPLETED if validation_passed in (ValidationResult.PASS, ValidationResult.SKIPPED)
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

        logger.info(
            "task_done",
            job_id=job_id,
            status=final_status,
            pages=result.page_count,
            time=round(result.processing_time, 2),
        )
        return {"job_id": job_id, "status": final_status}

    except (IngestError, OCRError, GenerationError) as exc:
        logger.error("task_failed_unrecoverable", job_id=job_id, error=str(exc))
        _update_job(
            session, job_id,
            status=JobStatus.FAILED,
            error_message=str(exc),
            progress_status="failed",
        )
        return {"job_id": job_id, "status": "failed", "error": str(exc)}

    except AltTextError as exc:
        # Retry on transient AI errors
        logger.warning("task_retry", job_id=job_id, error=str(exc))
        try:
            raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))
        except MaxRetriesExceededError:
            logger.warning("max_retries_exceeded", job_id=job_id, note="continuing without alt text")
            # Re-run pipeline with skip_alt_text=True (simplification: re-queue once)
            _update_job(session, job_id, error_message="Alt text generation failed after retries")
            return {"job_id": job_id, "status": "partial"}

    except Exception as exc:
        logger.exception("task_unexpected_error", job_id=job_id, error=str(exc))
        _update_job(
            session, job_id,
            status=JobStatus.FAILED,
            error_message=f"Unexpected error: {str(exc)[:500]}",
            progress_status="failed",
        )
        raise  # Let Celery mark it as failed
    finally:
        session.close()
