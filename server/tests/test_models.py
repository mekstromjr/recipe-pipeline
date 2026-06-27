"""ORM structure + enum persistence. No DB needed — live migration is validated in #4.

The constraint-name assertions are the guard that the hand-authored 0001 migration stays in
lockstep with the ORM's naming convention (so future `alembic revision --autogenerate` is clean).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from recipe_pipeline_server.db.models import Asset, Base, Job, RecipeRow
from recipe_pipeline_server.db.types import StrEnumType
from recipe_pipeline_shared.jobs import Job as JobDTO
from recipe_pipeline_shared.jobs import JobStatus, SourceKind


def test_tables_present():
    assert set(Base.metadata.tables) == {"jobs", "recipes", "assets"}
    assert Job.__tablename__ == "jobs"
    assert RecipeRow.__tablename__ == "recipes"
    assert Asset.__tablename__ == "assets"


def test_constraint_and_index_names_match_migration():
    names: set[str] = set()
    for table in Base.metadata.tables.values():
        names |= {c.name for c in table.constraints if c.name}
        names |= {ix.name for ix in table.indexes if ix.name}
    # exactly the names hard-coded in migrations/versions/0001_initial.py
    for expected in (
        "pk_jobs",
        "pk_recipes",
        "pk_assets",
        "fk_recipes_job_id_jobs",
        "fk_assets_job_id_jobs",
        "ck_jobs_attempts_non_negative",
        "ix_jobs_status",
        "ix_jobs_lease_expires_at",
        "ix_assets_job_id",
    ):
        assert expected in names, f"{expected} missing — migration/ORM name drift"


def test_strenumtype_roundtrips():
    t = StrEnumType(JobStatus, 20)
    assert t.process_bind_param(JobStatus.QUEUED, None) == "queued"
    assert t.process_bind_param("queued", None) == "queued"  # tolerate raw str
    assert t.process_result_value("queued", None) is JobStatus.QUEUED
    assert t.process_bind_param(None, None) is None
    assert t.process_result_value(None, None) is None


def test_job_dto_builds_from_orm_like_object():
    now = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
    row = Job(
        id=uuid.uuid4(),
        status=JobStatus.NEEDS_REVIEW,
        source_kind=SourceKind.PDF,
        source_name="grandmas-pie.pdf",
        attempts=1,
        created_at=now,
        updated_at=now,
    )
    dto = JobDTO.model_validate(row)
    assert dto.status is JobStatus.NEEDS_REVIEW
    assert dto.source_name == "grandmas-pie.pdf"
    assert dto.reviewed_by is None
