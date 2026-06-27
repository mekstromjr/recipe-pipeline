"""Metadata enrichment — ADDITIVE ONLY.

Uses the local model + SearXNG to add context that wasn't on the card: cuisine/genre, meal/
course, a one-paragraph dish history, and ESTIMATES for missing cook time / servings. This stage
NEVER alters the recipe's ingredients, quantities, or steps. Estimated scalar fields are written
with ``Provenance.ESTIMATED``; cuisine/meal/history are clearly enrichment. Only the dish NAME is
sent to web search — never the card's quantities. Best-effort: if SearXNG is unreachable the
recipe is returned unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel
from recipe_pipeline_shared.schema import Field_, Provenance

from recipe_pipeline import ollama_client, searxng_client

if TYPE_CHECKING:
    from recipe_pipeline_shared.schema import Recipe

    from recipe_pipeline.config import Settings

ENRICH_SYSTEM_PROMPT = """\
You add background metadata to a recipe. You are given the dish name and some web search
snippets. Using them plus general culinary knowledge, return metadata ONLY — never invent or
change the recipe's ingredients or steps. If a value is unknown, use null. Estimates (times,
servings) should be typical values for this dish, clearly approximate.
"""


class Enrichment(BaseModel):
    cuisine: str | None = None  # e.g. "American", "Italian"
    meal_course: str | None = None  # e.g. "Salad", "Dessert", "Main"
    dish_history: str | None = None  # one short paragraph
    servings_estimate: str | None = None
    prep_time_estimate: str | None = None
    cook_time_estimate: str | None = None
    total_time_estimate: str | None = None


def _set_if_empty(field: Field_, estimate: str | None) -> Field_:
    """Fill a timing/yield field with an ESTIMATE only if the card didn't provide one."""
    if estimate and field.value is None:
        return Field_(value=estimate, provenance=Provenance.ESTIMATED)
    return field


def enrich(settings: Settings, recipe: Recipe) -> Recipe:
    """Return ``recipe`` augmented with enriched/estimated fields (provenance-tagged). Additive."""
    try:
        results = searxng_client.search(settings, f"{recipe.name} recipe dish")
    except Exception as exc:  # noqa: BLE001 - enrichment is best-effort; never break the run
        print(f"  enrichment: SearXNG unavailable ({exc}); skipping web context")
        results = []

    snippets = "\n".join(
        f"- {r.get('title', '')}: {r.get('content', '')}" for r in results
    ) or "(no web results)"

    try:
        reply = ollama_client.chat(
            settings,
            system=ENRICH_SYSTEM_PROMPT,
            user=f"Dish name: {recipe.name}\n\nWeb snippets:\n{snippets}",
            model=settings.enrich_model,
            format=Enrichment.model_json_schema(),
        )
        data = Enrichment.model_validate_json(_json_only(reply))
    except Exception as exc:  # noqa: BLE001
        print(f"  enrichment: model step failed ({exc}); leaving recipe un-enriched")
        return recipe

    if data.cuisine:
        recipe.tags.append(data.cuisine)
    if data.meal_course:
        recipe.categories.append(data.meal_course)
    if data.dish_history:
        recipe.dish_history = data.dish_history
    recipe.servings = _set_if_empty(recipe.servings, data.servings_estimate)
    recipe.prep_time = _set_if_empty(recipe.prep_time, data.prep_time_estimate)
    recipe.cook_time = _set_if_empty(recipe.cook_time, data.cook_time_estimate)
    recipe.total_time = _set_if_empty(recipe.total_time, data.total_time_estimate)
    return recipe


def _json_only(text: str) -> str:
    s = text.strip()
    start, end = s.find("{"), s.rfind("}")
    return s[start : end + 1] if start != -1 and end != -1 else s
