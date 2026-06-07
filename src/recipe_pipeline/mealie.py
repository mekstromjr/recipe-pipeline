"""Mealie API client + payload mapping.

Verified against the live Mealie **v3.19.1** OpenAPI. Create is TWO calls:

    POST /api/recipes           body {"name": "..."}  (CreateRecipe; only `name` req) -> slug
    PATCH /api/recipes/{slug}   body Recipe-Input (full object)

Recipe-Input field mapping:
    name                 <- Recipe.name
    description          <- Recipe.description
    recipeIngredient[]   <- [{"note": <line>, "quantity": 0} ...]   + settings.disableAmount=true
    recipeInstructions[] <- [{"text": <step>} ...]                   (RecipeStep.text required)
    recipeServings       <- Recipe.servings.value (number)
    recipeYield          <- Recipe.recipe_yield.value (string, e.g. "4 servings")
    prepTime/cookTime/totalTime/performTime <- string times
    recipeCategory[]     <- [{"name", "slug"} ...]  (meal/course; both required)
    tags[]               <- [{"name", "slug"} ...]  (cuisine/genre; both required)
    notes[]              <- [{"title", "text"} ...]
    extras               <- {provenance, dish_history, ai_estimated_fields, ocr_confidence,
                             unclear_spans, source_curiosities}   # non-card content lives here
    settings.disableAmount = true  (free-text ingredients)

Image + original scan:
    PUT  /api/recipes/{slug}/image   (multipart)  <- page-1 rendered JPEG
    POST /api/recipes/{slug}/assets  (multipart)  <- the ORIGINAL scanned PDF (permanent record)

Auth: the `mealie` Service is plain ClusterIP:9000 with NO Authentik (that's only at the
ingress). Run `kubectl -n prod port-forward svc/mealie 9000:9000`; authenticate with a Mealie
long-lived API token (`Authorization: Bearer <token>`).

STUB.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings
    from recipe_pipeline.schema import Recipe


def recipe_to_payload(recipe: Recipe) -> dict[str, Any]:
    """Map our :class:`Recipe` to a Mealie ``Recipe-Input`` PATCH body (see module docstring)."""
    raise NotImplementedError("recipe_to_payload not implemented yet (see module docstring).")


class MealieClient:
    """Minimal Mealie API client (create + patch + image + asset upload)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def upload(self, recipe: Recipe, *, source_pdf: Path, page_one_image: Path) -> str:
        """Create + PATCH the recipe, set the image, attach the PDF. Return the new slug."""
        raise NotImplementedError(
            "Mealie upload not implemented yet. POST /api/recipes -> PATCH /api/recipes/{slug} "
            "-> PUT image -> POST asset. Mint an API token and port-forward svc/mealie."
        )
