"""Tests for the Mealie payload mapping (pure function)."""

from __future__ import annotations

from recipe_pipeline.mealie import recipe_to_payload
from recipe_pipeline.schema import Field_, Ingredient, Provenance, Recipe, Step


def test_recipe_to_payload_shape():
    r = Recipe(
        name="Apple Pie",
        ingredients=[Ingredient(text="2 cup crushed pineapple")],
        instructions=[Step(text="Mix and bake.")],
        recipe_yield=Field_(value="4 servings", provenance=Provenance.ESTIMATED),
        original_transcription="2 cup crushed pineapple\nMix and bake.",
    )
    tags = [{"id": "1", "name": "Scan", "slug": "scan"}]
    p = recipe_to_payload(r, tags=tags, categories=[])

    # name is set at create time, NOT in the PATCH body (re-sending it 400s on duplicates)
    assert "name" not in p
    # free-text ingredients + disableAmount
    assert p["recipeIngredient"] == [{"note": "2 cup crushed pineapple", "quantity": 0}]
    assert p["settings"]["disableAmount"] is True
    # Mealie needs the full step shape (bare {text} 500s server-side)
    assert p["recipeInstructions"] == [
        {"text": "Mix and bake.", "title": "", "summary": "", "ingredientReferences": []}
    ]
    # extras values must all be strings (Mealie's extras column rejects lists)
    assert all(isinstance(v, str) for v in p["extras"].values())
    assert p["tags"] == tags
    assert p["recipeYield"] == "4 servings"
    # verbatim transcription preserved in extras, not the canonical fields
    assert p["extras"]["original_transcription"].startswith("2 cup crushed pineapple")
    assert p["extras"]["recipe_pipeline"] == "v1"
