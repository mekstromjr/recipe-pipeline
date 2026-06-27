"""initial schema: jobs, recipes, assets

Revision ID: 0001
Revises:
Create Date: 2026-06-27
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_kind", sa.String(length=10), nullable=False),
        sa.Column("source_name", sa.String(length=512), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_by", sa.String(length=128), nullable=True),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        # bare token: the "ck" naming convention expands this to ck_jobs_attempts_non_negative
        sa.CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_lease_expires_at", "jobs", ["lease_expires_at"])

    op.create_table(
        "recipes",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_recipes_job_id_jobs", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("job_id", name="pk_recipes"),
    )

    op.create_table(
        "assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("s3_key", sa.String(length=1024), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_assets_job_id_jobs", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assets"),
    )
    op.create_index("ix_assets_job_id", "assets", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_assets_job_id", table_name="assets")
    op.drop_table("assets")
    op.drop_table("recipes")
    op.drop_index("ix_jobs_lease_expires_at", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_table("jobs")
