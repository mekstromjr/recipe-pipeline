"""Map the canonical :class:`Recipe` to a Mealie ``Recipe-Input`` PATCH body.

Shared between the worker CLI and the server's in-cluster injection so the hard-won payload
gotchas live in exactly one place. Verified against live Mealie **v3.19.1**:

- PATCH must NOT re-send ``name`` (set at create) — duplicate-named recipes 400
  ("Recipe already exists").
- ``recipeInstructions`` need the full step shape, not just ``{text}`` (bare -> 500).
- ``extras`` stores flat STRING values only (lists -> 500).
"""

from __future__ import annotations

from typing import Any

from recipe_pipeline_shared.schema import Recipe


def _times(recipe: Recipe) -> dict[str, Any]:
    out: dict[str, Any] = {}
    pairs = (("prep_time", "prepTime"), ("cook_time", "cookTime"), ("total_time", "totalTime"))
    for our, mealie in pairs:
        f = getattr(recipe, our)
        if f.value:
            out[mealie] = f.value
    return out


def recipe_to_payload(
    recipe: Recipe,
    *,
    tags: list[dict],
    categories: list[dict],
    ingredients: list[dict] | None = None,
) -> dict[str, Any]:
    """``tags``/``categories`` are pre-resolved Mealie organizer objects ({id,name,slug}).

    ``ingredients`` are pre-parsed Mealie ingredient objects (see
    ``MealieClient.parse_ingredients``). When omitted, every line is sent as one opaque note —
    the old behaviour, which left a human to run edit -> parse -> select in the Mealie UI for
    each recipe.
    """
    notes = []
    if recipe.source:
        notes.append({"title": "Source", "text": recipe.source})
    if recipe.import_note:
        notes.append({"title": "Import note", "text": recipe.import_note})

    extras = {
        "recipe_pipeline": "v1",
        "provenance": "scan",
        "ai_estimated_fields": ", ".join(recipe.estimated_fields()),
        "ocr_confidence": "" if recipe.ocr_confidence is None else str(recipe.ocr_confidence),
        "dish_history": recipe.dish_history or "",
        "source_curiosities": recipe.source_curiosities or "",
        "unclear_spans": " | ".join(recipe.unclear_spans),
        "original_transcription": recipe.original_transcription or "",
    }

    body: dict[str, Any] = {
        "description": recipe.description or (recipe.dish_history or ""),
        "recipeIngredient": (
            ingredients
            if ingredients is not None
            else [{"note": i.text, "quantity": 0} for i in recipe.ingredients]
        ),
        "recipeInstructions": [
            {"text": s.text, "title": "", "summary": "", "ingredientReferences": []}
            for s in recipe.instructions
        ],
        "recipeCategory": categories,
        "tags": tags,
        "notes": notes,
        "extras": {k: v for k, v in extras.items() if v not in (None, [], "")},
        # disableAmount is per-recipe and gates whether Mealie renders structured amounts at
        # all. Forcing True is what made parsed ingredients pointless; it is now True only
        # when nothing was parsed, so an unparsed import still displays exactly as before.
        "settings": {"disableAmount": ingredients is None},
        **_times(recipe),
    }
    if recipe.recipe_yield.value:
        body["recipeYield"] = recipe.recipe_yield.value
    return body


def model_tags(recipe) -> list[str]:
    """Bare names of the engines that processed this recipe, e.g. ``glm-ocr``, ``qwen3.5:4b``.

    Derived from the RECIPE, never from current config: models change (Apple Vision ->
    glm-ocr, gemma4 -> qwen3.5 inside one month), and a recipe must keep saying what
    actually read it. Recipes processed before these fields existed simply get no model tag
    rather than a wrong one.

    The ``:latest`` suffix is dropped because it names no particular version.
    """
    tags = []
    for value in (recipe.ocr_model, recipe.text_model):
        if value:
            tags.append(value.removesuffix(":latest"))
    return tags
