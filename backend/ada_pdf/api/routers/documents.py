"""Document conversion endpoints."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import hashlib

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ada_pdf.api.deps import get_current_api_key, get_db
from ada_pdf.api.schemas import JobCreateResponse, JobStatusResponse, ErrorResponse
from ada_pdf.models.orm import KeyStatus
from ada_pdf.models.orm import ApiKey, Job, JobStatus
from fastapi import BackgroundTasks

from ada_pdf.storage.local import LocalStorage
from ada_pdf.tasks.inline_task import process_inline
from ada_pdf.utils.file_validation import FileValidationError, validate_upload
from ada_pdf.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobCreateResponse,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
)
async def upload_document(
    file: UploadFile,
    request: Request,
    background_tasks: BackgroundTasks,
    doc_type: str = Form(default="auto"),        # auto | digital | scanned
    language: str = Form(default="auto"),         # auto | en | ar | fr | de | es | ...
    quality: str = Form(default="thorough"),           # fast | thorough
    conversion_mode: str = Form(default="rebuild"),    # rebuild | tag_in_place
    generate_alt_text: bool = Form(default=True),
    detect_forms: bool = Form(default=True),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(get_current_api_key),
):
    settings = request.app.state.settings
    storage = LocalStorage(settings.STORAGE_ROOT)

    data = await file.read()
    try:
        validate_upload(
            data,
            file.filename or "upload.pdf",
            settings.max_file_size_bytes,
            settings.CLAMAV_SOCKET_PATH,
        )
    except FileValidationError as exc:
        raise HTTPException(status_code=400, detail={"error": exc.code, "message": exc.message})

    # Check for password-protected PDF early
    try:
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        doc.close()
    except Exception as exc:
        if "password" in str(exc).lower() or "encrypted" in str(exc).lower():
            raise HTTPException(
                status_code=400,
                detail={"error": "password_protected", "message": "Remove PDF encryption before uploading"},
            )

    job_id = str(uuid.uuid4())
    input_path = storage.save_input(job_id, data, file.filename or "upload.pdf")

    options = {
        "doc_type": doc_type,
        "language": language,
        "quality": quality,
        "conversion_mode": conversion_mode,
        "generate_alt_text": generate_alt_text,
        "detect_forms": detect_forms,
    }

    now = datetime.now(timezone.utc)
    job = Job(
        id=uuid.UUID(job_id),
        status=JobStatus.PENDING,
        created_at=now,
        original_filename=file.filename,
        input_path=str(input_path),
        api_key_id=api_key.id,
        options_json=json.dumps(options),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Dispatch: inline BackgroundTask (SIMPLE_MODE) or Celery queue (full stack)
    if request.app.state.settings.SIMPLE_MODE:
        background_tasks.add_task(process_inline, job_id, options)
    else:
        # Lazy import keeps Celery out of the module-level import graph
        # so PyInstaller bundles work without bundling Celery.
        from ada_pdf.tasks.process_document import process_document  # noqa: PLC0415
        process_document.delay(job_id, options)
    logger.info("job_created", job_id=job_id, filename=file.filename, quality=quality)

    return JobCreateResponse(job_id=job.id, status=job.status, created_at=job.created_at)


@router.get("/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(get_current_api_key),
):
    job = await _get_job(db, job_id, api_key)
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        progress_pct=job.progress_pct,
        progress_status=job.progress_status,
        created_at=job.created_at,
        updated_at=job.updated_at,
        page_count=job.page_count,
        processing_time_seconds=job.processing_time_seconds,
        error_message=job.error_message,
        original_filename=job.original_filename,
        validation_result=job.validation_result,
    )


@router.get("/{job_id}/result")
async def download_result(
    job_id: uuid.UUID,
    request: Request,
    api_key_param: str | None = Query(default=None, alias="api_key"),
    x_api_key: str | None = Header(default=None),
    preview: bool = Query(default=False),   # ?preview=true → inline (for iframe)
    db: AsyncSession = Depends(get_db),
):
    # Authenticate via header (API clients) or query param (?api_key=…) (iframe preview)
    raw_key = x_api_key or api_key_param
    if not raw_key:
        raise HTTPException(status_code=401, detail="Authentication required")
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    res = await db.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash,
                             ApiKey.is_active == KeyStatus.ACTIVE)
    )
    api_key = res.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    job = await _get_job(db, job_id, api_key)

    if job.status == JobStatus.PENDING or job.status == JobStatus.PROCESSING:
        return Response(
            status_code=409,
            headers={"Retry-After": "5"},
            content='{"error":"processing","message":"Job is still processing"}',
            media_type="application/json",
        )

    if job.status == JobStatus.FAILED or not job.output_path:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "No output available — job may have failed"},
        )

    output_path = Path(job.output_path)
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Output file not found on disk")

    status_header = "compliant" if job.validation_result and job.validation_result.value == "pass" else "partial"
    orig = job.original_filename or "document.pdf"
    stem = orig.rsplit(".", 1)[0] if "." in orig else orig
    filename = f"{stem}_ADA_Accessible.pdf"

    def _iter():
        with open(output_path, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    # ?preview=true → inline so iframes can display; default → attachment to force download
    disposition = "inline" if preview else "attachment"
    headers = {
        "Content-Disposition": f'{disposition}; filename="{filename}"',
        "X-Validation-Status": status_header,
        "X-Original-Filename": filename,
    }

    http_status = 200  # always 200 — 206 was confusing browsers about content ranges
    return StreamingResponse(
        _iter(),
        media_type="application/pdf",
        status_code=http_status,
        headers=headers,
    )


@router.get("/{job_id}/report")
async def get_report(
    job_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(get_current_api_key),
):
    job = await _get_job(db, job_id, api_key)

    if job.status in (JobStatus.PENDING, JobStatus.PROCESSING):
        return Response(
            status_code=409,
            content='{"error":"processing","message":"Report not ready yet"}',
            media_type="application/json",
        )

    if not job.report_path:
        raise HTTPException(status_code=404, detail="Validation report not available")

    report_path = Path(job.report_path)
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report file not found on disk")

    accept = request.headers.get("accept", "application/json")
    report_data = report_path.read_bytes()

    if "text/html" in accept:
        # Re-build HTML from JSON
        from ada_pdf.validation.report_builder import AccessibilityReport
        report_obj = AccessibilityReport.model_validate_json(report_data)
        return Response(content=report_obj.to_html(), media_type="text/html")

    return Response(content=report_data, media_type="application/json")


async def _get_job(db: AsyncSession, job_id: uuid.UUID, api_key: ApiKey) -> Job:
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.api_key_id != api_key.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return job
