"""Metadata enrichment — ADDITIVE ONLY.

Uses the local model + SearXNG to add context that wasn't on the card: cuisine/genre, meal/
course, a one-paragraph dish history, and ESTIMATES for missing cook time / servings. This
stage must NEVER alter the recipe's ingredients, quantities, or steps. Estimated scalar fields
are written with ``Provenance.ESTIMATED`` and history/tags are clearly enrichment, so they land
in Mealie's ``extras``/organizers rather than masquerading as card content.

Only the dish NAME is sent to web search — never the card's quantities.

STUB.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings
    from recipe_pipeline.schema import Recipe


def enrich(settings: Settings, recipe: Recipe) -> Recipe:
    """Return ``recipe`` augmented with enriched/estimated fields (provenance-tagged). Additive."""
    raise NotImplementedError(
        "Enrichment not implemented yet. Query SearXNG for the dish name, summarize with the "
        "local model; set cuisine/meal/history + estimate missing times/servings as "
        "Provenance.ESTIMATED. Do not touch ingredients/steps."
    )
