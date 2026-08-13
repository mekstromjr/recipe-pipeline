"""Review report on recipes + mealie_slug on jobs (#8).

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recipes", sa.Column("review", postgresql.JSONB(), nullable=True))
    op.add_column("jobs", sa.Column("mealie_slug", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "mealie_slug")
    op.drop_column("recipes", "review")
