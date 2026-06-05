"""Periodic cleanup task — purge converted files older than the configured TTL."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from ada_pdf.tasks.celery_app import celery_app
from ada_pdf.utils.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_TTL_HOURS = 24


@celery_app.task(name="ada_pdf.tasks.cleanup.purge_old_jobs", ignore_result=True)
def purge_old_jobs(ttl_hours: int = _DEFAULT_TTL_HOURS) -> None:
    """Delete job directories in STORAGE_ROOT older than ttl_hours.

    Each job lives in STORAGE_ROOT/<job_id>/.  We check the directory mtime
    and remove any that are past the TTL.  The database records are left intact
    (soft-delete by orphaning the output_path reference).
    """
    from ada_pdf.config import get_settings
    settings = get_settings()
    storage_root = Path(settings.STORAGE_ROOT)
    if not storage_root.exists():
        return

    cutoff = datetime.now(timezone.utc).timestamp() - ttl_hours * 3600
    removed = 0
    errors = 0

    for job_dir in storage_root.iterdir():
        if not job_dir.is_dir():
            continue
        try:
            mtime = job_dir.stat().st_mtime
            if mtime < cutoff:
                shutil.rmtree(job_dir, ignore_errors=True)
                removed += 1
        except Exception as exc:
            logger.warning("cleanup_dir_error", path=str(job_dir), error=str(exc))
            errors += 1

    logger.info("cleanup_complete", removed=removed, errors=errors,
                ttl_hours=ttl_hours)
