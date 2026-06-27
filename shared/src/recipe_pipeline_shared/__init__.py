"""Shared models for the recipe service.

This package is the ONE source of truth imported by BOTH the MacBook worker
(``recipe_pipeline``) and the k8s server (``recipe_pipeline_server``): the worker
produces exactly what the server stores and ships to Mealie. Keep it dependency-light
(pydantic + stdlib only) so the server image never pulls worker-only OCR deps.
"""

from recipe_pipeline_shared.jobs import (
    AssetKind,
    InvalidTransition,
    Job,
    JobStatus,
    SourceKind,
    can_transition,
    ensure_transition,
    is_terminal,
)
from recipe_pipeline_shared.schema import Recipe
from recipe_pipeline_shared.slug import slugify

__all__ = [
    "AssetKind",
    "InvalidTransition",
    "Job",
    "JobStatus",
    "Recipe",
    "SourceKind",
    "can_transition",
    "ensure_transition",
    "is_terminal",
    "slugify",
]
