"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_hash", sa.String(256), nullable=False),
        sa.Column("name", sa.String(256), nullable=True),
        sa.Column(
            "is_active",
            sa.Enum("active", "revoked", name="keystatus"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "processing", "completed", "failed", "partial", name="jobstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("original_filename", sa.String(512), nullable=True),
        sa.Column("input_path", sa.String(1024), nullable=True),
        sa.Column("output_path", sa.String(1024), nullable=True),
        sa.Column("report_path", sa.String(1024), nullable=True),
        sa.Column("progress_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_status", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("processing_time_seconds", sa.Float(), nullable=True),
        sa.Column("api_key_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "validation_result",
            sa.Enum("pass", "fail", "partial", "skipped", name="validationresult"),
            nullable=True,
            server_default="skipped",
        ),
        sa.Column("celery_task_id", sa.String(256), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_api_key_id", "jobs", ["api_key_id"])


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("api_keys")
    op.execute("DROP TYPE IF EXISTS jobstatus")
    op.execute("DROP TYPE IF EXISTS validationresult")
    op.execute("DROP TYPE IF EXISTS keystatus")
