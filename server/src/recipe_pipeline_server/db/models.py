"""SQLAlchemy ORM for the recipe service (server-side only).

Three tables:
- ``jobs``    : the lifecycle row (status, lease, audit stamps).
- ``recipes`` : the structured recipe as JSONB (one per job; NOT normalized — the nested
                Recipe model is stored whole and round-trips through pydantic).
- ``assets``  : pointers into the Garage ``recipes`` bucket (original source + page images).

Statuses use ``native_enum=False`` so they persist as VARCHAR + CHECK rather than a Postgres
ENUM type — adding a status later is a plain migration, not an ``ALTER TYPE`` dance.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from recipe_pipeline_shared.jobs import AssetKind, JobStatus, SourceKind
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from recipe_pipeline_server.db.types import StrEnumType

# Stable, predictable constraint/index names so Alembic autogenerate diffs cleanly.
_NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    status: Mapped[JobStatus] = mapped_column(
        StrEnumType(JobStatus, length=20), nullable=False, index=True
    )
    source_kind: Mapped[SourceKind] = mapped_column(
        StrEnumType(SourceKind, length=10), nullable=False
    )
    source_name: Mapped[str] = mapped_column(String(512), nullable=False)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Lease (visibility timeout): a claimed job not completed by lease_expires_at returns to queued.
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # Audit stamps (Authentik usernames).
    uploaded_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Set when injection into Mealie succeeds; also the #14 backfill dedup key.
    mealie_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    recipe: Mapped[RecipeRow | None] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )
    assets: Mapped[list[Asset]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    )


class RecipeRow(Base):
    __tablename__ = "recipes"

    # One recipe per job: the job id IS the primary key.
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # The worker's ReviewReport (status/confidence/flags) as dumped JSON — kept separate from
    # the Recipe itself so the canonical recipe stays exactly what goes to Mealie.
    review: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    job: Mapped[Job] = relationship(back_populates="recipe")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[AssetKind] = mapped_column(
        StrEnumType(AssetKind, length=10), nullable=False
    )
    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    job: Mapped[Job] = relationship(back_populates="assets")
