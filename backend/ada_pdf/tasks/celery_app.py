"""Celery application factory."""
import os

from celery import Celery

broker = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/1")
backend = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")

celery_app = Celery(
    "ada_pdf",
    broker=broker,
    backend=backend,
    include=["ada_pdf.tasks.process_document", "ada_pdf.tasks.cleanup"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # PyTorch/Surya requires spawn, not fork
    worker_pool="solo",
    beat_schedule={
        "purge-old-jobs-daily": {
            "task": "ada_pdf.tasks.cleanup.purge_old_jobs",
            "schedule": 86400,   # every 24 hours
            "kwargs": {"ttl_hours": 24},
        },
    },
)
