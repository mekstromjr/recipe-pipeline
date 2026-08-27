"""Source content hash for upload dedup (#6).

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("source_sha256", sa.String(length=64), nullable=True))
    op.create_index("ix_jobs_source_sha256", "jobs", ["source_sha256"])


def downgrade() -> None:
    op.drop_index("ix_jobs_source_sha256", table_name="jobs")
    op.drop_column("jobs", "source_sha256")
