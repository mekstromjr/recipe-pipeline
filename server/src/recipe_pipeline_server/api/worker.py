"""Worker API (#7): the pull-only contract for the OCR worker.

The cluster NEVER initiates a connection to the worker (that direction is the flaky one) —
the worker claims work, downloads sources, and posts results, holding exactly one
credential: the static worker key, validated here. These routes live under ``/api/worker``,
which the ingress serves WITHOUT Authentik forward-auth.

Lifecycle: claim leases the oldest queued PDF job (``FOR UPDATE SKIP LOCKED`` so two
workers can never claim the same job); a lease that expires returns the job to ``queued``
via a lazy sweep at the next claim — no background scheduler. Result submission is
idempotent: retrying after a network blip is a no-op, and a job that was reclaimed by
someone else in the meantime is refused with 409.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Response, UploadFile
from pydantic import BaseModel, ValidationError
from recipe_pipeline_shared.jobs import JobStatus, SourceKind, ensure_transition
from recipe_pipeline_shared.schema import Recipe, ReviewReport
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from recipe_pipeline_server.api.deps import SettingsDep, StorageDep
from recipe_pipeline_server.db.models import Asset, Job, RecipeRow
from recipe_pipeline_server.db.session import get_session
from recipe_pipeline_server.storage import page_key

logger = logging.getLogger("recipe_service")


def require_worker_key(
    x_worker_key: Annotated[str | None, Header()] = None,
    settings: SettingsDep = None,
) -> None:
    if not settings.worker_api_key:
        raise HTTPException(503, "worker API key not configured server-side")
    if x_worker_key != settings.worker_api_key:
        raise HTTPException(401, "bad or missing X-Worker-Key")


router = APIRouter(
    prefix="/api/worker", tags=["worker"], dependencies=[Depends(require_worker_key)]
)

SessionDep = Depends(get_session)


class ClaimRequest(BaseModel):
    worker: str


class ClaimedJob(BaseModel):
    id: str
    source_name: str
    source_kind: SourceKind
    attempts: int
    lease_expires_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


async def _get_job(session: AsyncSession, job_id: str) -> Job:
    try:
        key = uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(404, "no such job") from exc
    job = await session.get(Job, key)
    if job is None:
        raise HTTPException(404, "no such job")
    return job


@router.post("/claim", response_model=None)
async def claim(
    req: ClaimRequest, settings: SettingsDep, session: AsyncSession = SessionDep
) -> ClaimedJob | Response:
    # Lazy sweep: give back expired leases before handing out work. Bulk UPDATE is safe —
    # processing -> queued is the FSM's lease-expiry edge.
    swept = await session.execute(
        update(Job)
        .where(Job.status == JobStatus.PROCESSING, Job.lease_expires_at < _now())
        .values(status=JobStatus.QUEUED, lease_owner=None, lease_expires_at=None)
    )
    if swept.rowcount:
        logger.warning("lease sweep: %d expired job(s) returned to queued", swept.rowcount)

    job = (
        await session.execute(
            select(Job)
            .where(Job.status == JobStatus.QUEUED, Job.source_kind == SourceKind.PDF)
            .order_by(Job.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar()
    if job is None:
        await session.commit()
        return Response(status_code=204)
    job.status = ensure_transition(job.status, JobStatus.PROCESSING)
    job.lease_owner = req.worker
    job.lease_expires_at = _now() + timedelta(seconds=settings.worker_lease_seconds)
    job.attempts += 1
    await session.commit()
    await session.refresh(job)
    logger.info("job %s claimed by %s (attempt %d)", job.id, req.worker, job.attempts)
    return ClaimedJob(
        id=str(job.id),
        source_name=job.source_name,
        source_kind=job.source_kind,
        attempts=job.attempts,
        lease_expires_at=job.lease_expires_at,
    )


@router.get("/jobs/{job_id}/source")
async def download_source(
    job_id: str, storage: StorageDep, session: AsyncSession = SessionDep
) -> Response:
    job = await _get_job(session, job_id)
    source = next((a for a in await _assets(session, job) if a.kind == "source"), None)
    if source is None:
        raise HTTPException(404, "job has no source asset")
    blob = await asyncio.to_thread(storage.get_bytes, source.s3_key)
    return Response(content=blob, media_type=source.content_type or "application/octet-stream")


async def _assets(session: AsyncSession, job: Job) -> list[Asset]:
    return list(
        (await session.execute(select(Asset).where(Asset.job_id == job.id))).scalars()
    )


def _require_owner(job: Job, worker: str) -> None:
    if job.lease_owner != worker:
        raise HTTPException(
            409, f"job is leased to {job.lease_owner!r}, not {worker!r} (lease lost?)"
        )


@router.post("/jobs/{job_id}/renew")
async def renew_lease(
    job_id: str, req: ClaimRequest, settings: SettingsDep, session: AsyncSession = SessionDep
) -> dict:
    job = await _get_job(session, job_id)
    if job.status is not JobStatus.PROCESSING:
        raise HTTPException(409, f"job is {job.status}, not processing")
    _require_owner(job, req.worker)
    job.lease_expires_at = _now() + timedelta(seconds=settings.worker_lease_seconds)
    await session.commit()
    return {"lease_expires_at": job.lease_expires_at.isoformat()}


@router.post("/jobs/{job_id}/result")
async def submit_result(
    job_id: str,
    worker: Annotated[str, Form()],
    recipe: Annotated[str, Form()],
    review: Annotated[str, Form()],
    storage: StorageDep,
    pages: list[UploadFile] | None = None,
    session: AsyncSession = SessionDep,
) -> dict:
    job = await _get_job(session, job_id)
    if job.status is JobStatus.NEEDS_REVIEW:
        # Idempotent retry: the previous POST landed but the response was lost.
        return {"status": job.status.value, "already": True}
    if job.status is not JobStatus.PROCESSING:
        raise HTTPException(409, f"job is {job.status}; cannot accept a result")
    _require_owner(job, worker)
    try:
        recipe_model = Recipe.model_validate(json.loads(recipe))
        review_model = ReviewReport.model_validate(json.loads(review))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(422, f"invalid recipe/review payload: {exc}") from exc

    # Reprocess-safe: a job that came back through needs_review -> queued already has a
    # recipe row and page assets; replace rather than violate the PK.
    for old_page in [a for a in await _assets(session, job) if a.kind == "page"]:
        await session.delete(old_page)
    page_assets = []
    for n, page in enumerate(pages or [], 1):
        blob = await page.read()
        key = page_key(str(job.id), n)
        await asyncio.to_thread(storage.put_bytes, key, blob, "image/jpeg")
        page_assets.append(Asset(job_id=job.id, kind="page", s3_key=key, content_type="image/jpeg"))
    session.add_all(page_assets)
    row = await session.get(RecipeRow, job.id)
    if row is None:
        row = RecipeRow(job_id=job.id, data={}, review=None)
        session.add(row)
    row.data = recipe_model.model_dump(mode="json")
    row.review = review_model.model_dump(mode="json")
    job.status = ensure_transition(job.status, JobStatus.NEEDS_REVIEW)
    job.lease_owner = None
    job.lease_expires_at = None
    job.error = None
    await session.commit()
    logger.info("job %s done by %s -> needs_review (%d pages)", job.id, worker, len(page_assets))
    return {"status": JobStatus.NEEDS_REVIEW.value, "already": False}


class FailRequest(BaseModel):
    worker: str
    error: str


@router.post("/jobs/{job_id}/fail")
async def fail_job(
    job_id: str, req: FailRequest, session: AsyncSession = SessionDep
) -> dict:
    job = await _get_job(session, job_id)
    if job.status is JobStatus.FAILED:
        return {"status": "failed", "already": True}
    if job.status is not JobStatus.PROCESSING:
        raise HTTPException(409, f"job is {job.status}; cannot fail it")
    _require_owner(job, req.worker)
    job.status = ensure_transition(job.status, JobStatus.FAILED)
    job.error = req.error[:2000]
    job.lease_owner = None
    job.lease_expires_at = None
    await session.commit()
    logger.warning("job %s failed by %s: %s", job.id, req.worker, req.error[:200])
    return {"status": "failed", "already": False}
