"""The job state machine is the spine of the service; pin its legal/illegal edges."""

from __future__ import annotations

from itertools import pairwise

import pytest
from recipe_pipeline_shared.jobs import (
    TERMINAL,
    InvalidTransition,
    JobStatus,
    can_transition,
    ensure_transition,
    is_terminal,
)


def test_happy_path_is_legal():
    path = [
        JobStatus.QUEUED,
        JobStatus.PROCESSING,
        JobStatus.NEEDS_REVIEW,
        JobStatus.APPROVED,
        JobStatus.UPLOADED,
    ]
    for src, dst in pairwise(path):
        assert can_transition(src, dst), f"{src}->{dst} should be legal"


def test_lease_expiry_and_retry_edges():
    # a dropped worker's lease expires: processing -> queued
    assert can_transition(JobStatus.PROCESSING, JobStatus.QUEUED)
    # a failed card can be re-queued (cloud escalation / retry)
    assert can_transition(JobStatus.FAILED, JobStatus.QUEUED)
    # a human can send a reviewed card back for reprocessing
    assert can_transition(JobStatus.NEEDS_REVIEW, JobStatus.QUEUED)
    # a failed in-cluster injection moves approved -> failed
    assert can_transition(JobStatus.APPROVED, JobStatus.FAILED)


def test_terminal_states_have_no_exits():
    assert frozenset({JobStatus.UPLOADED, JobStatus.REJECTED}) == TERMINAL
    for term in TERMINAL:
        assert is_terminal(term)
        for other in JobStatus:
            assert not can_transition(term, other), f"{term} must be terminal"


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (JobStatus.QUEUED, JobStatus.UPLOADED),       # can't skip processing/review
        (JobStatus.PROCESSING, JobStatus.APPROVED),   # must pass through review
        (JobStatus.QUEUED, JobStatus.REJECTED),       # only review can reject
        (JobStatus.UPLOADED, JobStatus.QUEUED),       # terminal
    ],
)
def test_illegal_transitions_raise(src, dst):
    assert not can_transition(src, dst)
    with pytest.raises(InvalidTransition):
        ensure_transition(src, dst)


def test_ensure_transition_returns_dst_when_legal():
    assert ensure_transition(JobStatus.QUEUED, JobStatus.PROCESSING) is JobStatus.PROCESSING
