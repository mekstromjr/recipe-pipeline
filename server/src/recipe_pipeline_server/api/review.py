"""Review API: the queue, the editor round-trip, and the approval path (#8).

Approve is the reliability win of the whole redesign: injection into Mealie happens HERE,
in-cluster, synchronously — the reviewer sees success or failure immediately, and the job row
records the outcome through the shared state machine (needs_review -> approved -> uploaded,
or approved -> failed with the error preserved).
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from recipe_pipeline_shared.jobs import InvalidTransition, JobStatus, ensure_transition
from recipe_pipeline_shared.jobs import Job as JobDTO
from recipe_pipeline_shared.schema import Ingredient, Recipe, ReviewReport, Step
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from recipe_pipeline_server.api.deps import SettingsDep, StorageDep, UserDep
from recipe_pipeline_server.db.models import Job, RecipeRow
from recipe_pipeline_server.db.session import get_session
from recipe_pipeline_server.mealie import MealieClient, MealieError
from recipe_pipeline_server.storage import page_key

logger = logging.getLogger("recipe_service")

router = APIRouter(prefix="/api", tags=["review"])

SessionDep = Depends(get_session)


class QueueItem(BaseModel):
    id: str
    name: str
    confidence: float
    flags: list[str]


class RecipeDetail(BaseModel):
    id: str
    status: JobStatus
    name: str
    description: str
    ingredients: list[str]
    instructions: list[str]
    verbatim: str
    confidence: float | None
    flags: list[str]
    pages: list[int]
    reviewed_by: str | None
    mealie_slug: str | None
    error: str | None


class RecipeEdit(BaseModel):
    """Fields the editor exposes. Everything else in the stored Recipe is preserved."""

    name: str | None = None
    description: str | None = None
    ingredients: list[str] | None = None
    instructions: list[str] | None = None


def _review(row: RecipeRow) -> ReviewReport | None:
    return ReviewReport.model_validate(row.review) if row.review else None


async def _get_job(session: AsyncSession, job_id: str) -> Job:
    try:
        key = uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(404, "no such job") from exc
    job = await session.get(
        Job, key, options=[selectinload(Job.recipe), selectinload(Job.assets)]
    )
    if job is None or job.recipe is None:
        raise HTTPException(404, "no such job")
    return job


def _transition(job: Job, dst: JobStatus) -> None:
    try:
        job.status = ensure_transition(job.status, dst)
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/queue")
async def queue(session: AsyncSession = SessionDep) -> list[QueueItem]:
    """The needs_review backlog, easiest-first (highest confidence)."""
    rows = (
        (
            await session.execute(
                select(Job)
                .where(Job.status == JobStatus.NEEDS_REVIEW)
                .options(selectinload(Job.recipe))
            )
        )
        .scalars()
        .all()
    )
    items = []
    for job in rows:
        if job.recipe is None:
            continue
        review = _review(job.recipe)
        items.append(
            QueueItem(
                id=str(job.id),
                name=job.recipe.data.get("name") or "(no name)",
                confidence=(review.confidence if review and review.confidence else 0.0),
                flags=sorted({f.code for f in review.flags}) if review else [],
            )
        )
    items.sort(key=lambda x: -x.confidence)
    return items


@router.get("/recipes/{job_id}")
async def get_recipe(job_id: str, session: AsyncSession = SessionDep) -> RecipeDetail:
    job = await _get_job(session, job_id)
    recipe = Recipe.model_validate(job.recipe.data)
    review = _review(job.recipe)
    pages = sorted(
        int(a.s3_key.rsplit("page-", 1)[-1].removesuffix(".jpg"))
        for a in job.assets
        if a.kind == "page"
    )
    return RecipeDetail(
        id=str(job.id),
        status=job.status,
        name=recipe.name,
        description=recipe.description or "",
        ingredients=[i.text for i in recipe.ingredients],
        instructions=[s.text for s in recipe.instructions],
        verbatim=recipe.original_transcription or "",
        confidence=review.confidence if review else None,
        flags=sorted({f.code for f in review.flags}) if review else [],
        pages=pages,
        reviewed_by=job.reviewed_by,
        mealie_slug=job.mealie_slug,
        error=job.error,
    )


@router.get("/recipes/{job_id}/pages/{page}")
async def get_page_image(
    job_id: str, page: int, storage: StorageDep, session: AsyncSession = SessionDep
) -> Response:
    job = await _get_job(session, job_id)
    try:
        blob = await asyncio.to_thread(storage.get_bytes, page_key(str(job.id), page))
    except Exception as exc:
        raise HTTPException(404, "page image not found") from exc
    return Response(content=blob, media_type="image/jpeg")


@router.patch("/recipes/{job_id}")
async def edit_recipe(
    job_id: str, edit: RecipeEdit, user: UserDep, session: AsyncSession = SessionDep
) -> dict:
    """Apply editor changes into the stored Recipe, preserving unexposed fields."""
    job = await _get_job(session, job_id)
    if job.status is not JobStatus.NEEDS_REVIEW:
        raise HTTPException(409, f"job is {job.status}, not editable")
    recipe = Recipe.model_validate(job.recipe.data)
    if edit.name is not None and edit.name.strip():
        recipe.name = edit.name.strip()
    if edit.description is not None:
        recipe.description = edit.description or None
    if edit.ingredients is not None:
        recipe.ingredients = [Ingredient(text=t) for t in edit.ingredients if t.strip()]
    if edit.instructions is not None:
        recipe.instructions = [Step(text=t) for t in edit.instructions if t.strip()]
    # Reassign (not mutate) so SQLAlchemy sees the JSONB change.
    job.recipe.data = recipe.model_dump(mode="json")
    await session.commit()
    logger.info("recipe %s edited by %s", job_id, user)
    return {"ok": True}


@router.post("/recipes/{job_id}/approve")
async def approve(
    job_id: str,
    request: Request,
    user: UserDep,
    settings: SettingsDep,
    storage: StorageDep,
    session: AsyncSession = SessionDep,
) -> JobDTO:
    job = await _get_job(session, job_id)
    _transition(job, JobStatus.APPROVED)
    job.reviewed_by = user
    await session.commit()

    recipe = Recipe.model_validate(job.recipe.data)
    source: tuple[str, bytes] | None = None
    page1: bytes | None = None
    for asset in job.assets:
        if asset.kind == "source":
            blob = await asyncio.to_thread(storage.get_bytes, asset.s3_key)
            source = (asset.s3_key.rsplit("/", 1)[-1], blob)
        elif asset.kind == "page" and asset.s3_key.endswith("page-1.jpg"):
            page1 = await asyncio.to_thread(storage.get_bytes, asset.s3_key)

    client: MealieClient = request.app.state.mealie_client_factory()
    try:
        slug = await client.upload(recipe, source=source, page_one_image=page1)
    except MealieError as exc:
        _transition(job, JobStatus.FAILED)
        job.error = str(exc)[:2000]
        await session.commit()
        raise HTTPException(502, f"approved, but Mealie injection failed: {exc}") from exc
    finally:
        await client.close()

    _transition(job, JobStatus.UPLOADED)
    job.mealie_slug = slug
    job.error = None
    await session.commit()
    # updated_at is a server-side onupdate: expired by the flush, so reload before
    # serializing (lazy attribute IO outside the greenlet context raises MissingGreenlet).
    await session.refresh(job)
    logger.info("recipe %s approved by %s -> mealie/%s", job_id, user, slug)
    return JobDTO.model_validate(job)


@router.post("/recipes/{job_id}/reject")
async def reject(job_id: str, user: UserDep, session: AsyncSession = SessionDep) -> JobDTO:
    """Human says: not a recipe / not worth keeping. Terminal."""
    job = await _get_job(session, job_id)
    _transition(job, JobStatus.REJECTED)
    job.reviewed_by = user
    await session.commit()
    await session.refresh(job)  # reload server-side updated_at (see approve)
    logger.info("recipe %s rejected by %s", job_id, user)
    return JobDTO.model_validate(job)


class StatusSummary(BaseModel):
    counts: dict[str, int]
    recent: list[dict]


@router.get("/status")
async def status_summary(session: AsyncSession = SessionDep) -> StatusSummary:
    """Counts by state + recent decisions, for the dashboard."""
    jobs = (await session.execute(select(Job))).scalars().all()
    counts: dict[str, int] = {}
    for j in jobs:
        counts[j.status.value] = counts.get(j.status.value, 0) + 1
    decided = [j for j in jobs if j.status in (JobStatus.UPLOADED, JobStatus.REJECTED)]
    decided.sort(key=lambda j: j.updated_at, reverse=True)
    recent = [
        {
            "id": str(j.id),
            "source_name": j.source_name,
            "status": j.status.value,
            "by": j.reviewed_by,
            "at": j.updated_at.isoformat(),
            "mealie_slug": j.mealie_slug,
        }
        for j in decided[:20]
    ]
    return StatusSummary(counts=counts, recent=recent)
