"""Job lifecycle: the state machine + DTOs shared by the worker and the server.

This is the spine of the service. The server persists jobs; the worker advances them by
calling the worker API. Both sides agree on these statuses and the legal transitions, so a
bad transition is a programming error caught here rather than silent corruption in the DB.

State machine::

    queued ──► processing ──► needs_review ──► approved ──► uploaded   (happy path)
                 │  ▲              │                │
                 │  │ (lease       │ (reprocess)    │ (injection failed)
        (fail)   ▼  │  expiry)     ▼                ▼
              failed ─────────────────────────────► queued
                                  rejected (human: not a recipe)

`uploaded` and `rejected` are terminal. `failed` is recoverable: cloud escalation (or a retry)
re-queues it. A `processing` job whose lease expires returns to `queued` so a worker that drops
mid-job self-heals.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    UPLOADED = "uploaded"
    FAILED = "failed"
    REJECTED = "rejected"


class SourceKind(StrEnum):
    """What was uploaded for a recipe."""

    PDF = "pdf"
    IMAGE = "image"  # single JPEG/HEIC photo


class AssetKind(StrEnum):
    """Objects stored in the `recipes` bucket for a job."""

    SOURCE = "source"  # the original uploaded PDF/image (permanent record)
    PAGE = "page"      # a rendered/upright page image for display + review


# Legal transitions. Anything not listed is rejected by :func:`can_transition`.
TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.PROCESSING}),
    # PROCESSING -> QUEUED is the lease-expiry / give-back edge.
    JobStatus.PROCESSING: frozenset(
        {JobStatus.NEEDS_REVIEW, JobStatus.FAILED, JobStatus.QUEUED}
    ),
    # NEEDS_REVIEW -> QUEUED lets a human (or escalation) request a reprocess.
    JobStatus.NEEDS_REVIEW: frozenset(
        {JobStatus.APPROVED, JobStatus.REJECTED, JobStatus.QUEUED}
    ),
    # APPROVED -> FAILED if the in-cluster Mealie injection errors.
    JobStatus.APPROVED: frozenset({JobStatus.UPLOADED, JobStatus.FAILED}),
    # FAILED is recoverable: escalate/retry re-queues it.
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
    JobStatus.UPLOADED: frozenset(),
    JobStatus.REJECTED: frozenset(),
}

#: Statuses from which no further transition is possible.
TERMINAL: frozenset[JobStatus] = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)


class InvalidTransition(ValueError):
    """Raised when an illegal status transition is attempted."""

    def __init__(self, src: JobStatus, dst: JobStatus) -> None:
        super().__init__(f"illegal job transition: {src} -> {dst}")
        self.src = src
        self.dst = dst


def can_transition(src: JobStatus, dst: JobStatus) -> bool:
    """True if moving a job from ``src`` to ``dst`` is legal."""
    return dst in TRANSITIONS[src]


def ensure_transition(src: JobStatus, dst: JobStatus) -> JobStatus:
    """Return ``dst`` if the transition is legal, else raise :class:`InvalidTransition`."""
    if not can_transition(src, dst):
        raise InvalidTransition(src, dst)
    return dst


def is_terminal(status: JobStatus) -> bool:
    return status in TERMINAL


class Job(BaseModel):
    """API/wire view of a job row (built from the ORM via ``from_attributes``)."""

    model_config = {"from_attributes": True}

    id: UUID
    status: JobStatus
    source_kind: SourceKind
    source_name: str
    attempts: int = 0
    error: str | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    uploaded_by: str | None = None
    reviewed_by: str | None = None
    mealie_slug: str | None = None
    created_at: datetime
    updated_at: datetime
