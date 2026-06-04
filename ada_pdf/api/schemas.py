"""Pydantic schemas for API request/response shapes."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel

from ada_pdf.models.orm import JobStatus, ValidationResult
from ada_pdf.validation.report_builder import AccessibilityReport


class JobCreateResponse(BaseModel):
    job_id: UUID
    status: JobStatus
    created_at: datetime


class JobStatusResponse(BaseModel):
    job_id: UUID
    status: JobStatus
    progress_pct: int
    progress_status: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]
    page_count: Optional[int]
    processing_time_seconds: Optional[float]
    error_message: Optional[str]
    validation_result: Optional[ValidationResult]
    original_filename: Optional[str] = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyzResponse(BaseModel):
    status: Literal["ok", "degraded"]
    db: bool
    redis: bool


class ErrorResponse(BaseModel):
    error: str
    message: str
    detail: Optional[str] = None
