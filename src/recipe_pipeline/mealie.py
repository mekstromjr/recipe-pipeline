"""Mealie API client + batch upload.

Verified against the live Mealie **v3.19.1** OpenAPI. Create is TWO calls:

    POST /api/recipes           body {"name": "..."}  (CreateRecipe; only `name` req) -> slug
    PATCH /api/recipes/{slug}   body Recipe-Input (full object)

Recipe-Input mapping: name, description; recipeIngredient[]=[{note, quantity:0}] with
settings.disableAmount=true; recipeInstructions[]=[{text}]; recipeServings/recipeYield;
prep/cook/total Time (strings); recipeCategory[]/tags[]=[{name,slug}]; notes[]=[{title,text}];
extras (free-form: provenance, history, estimates, unclear, verbatim transcription).

Media (verified multipart fields):
    PUT  /api/recipes/{slug}/image   image=<file>, extension=<ext>           <- page-1 upright JPEG
    POST /api/recipes/{slug}/assets  name, icon, extension, file=<file>      <- ORIGINAL PDF

Auth: `kubectl -n prod port-forward svc/mealie 9000:9000` + a Mealie API token
(RECIPE_MEALIE_API_TOKEN). The Service has no Authentik (that's only at the ingress).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from recipe_pipeline.ingest import slugify

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings
    from recipe_pipeline.schema import Recipe

logger = logging.getLogger("recipe_pipeline")


def _times(recipe: Recipe) -> dict[str, Any]:
    out: dict[str, Any] = {}
    pairs = (("prep_time", "prepTime"), ("cook_time", "cookTime"), ("total_time", "totalTime"))
    for our, mealie in pairs:
        f = getattr(recipe, our)
        if f.value:
            out[mealie] = f.value
    return out


def recipe_to_payload(
    recipe: Recipe, *, tags: list[dict], categories: list[dict]
) -> dict[str, Any]:
    """Map our :class:`Recipe` to a Mealie ``Recipe-Input`` PATCH body. ``tags``/``categories``
    are pre-resolved Mealie organizer objects ({id,name,slug})."""
    notes = []
    if recipe.source:
        notes.append({"title": "Source", "text": recipe.source})
    if recipe.import_note:
        notes.append({"title": "Import note", "text": recipe.import_note})

    extras = {
        "recipe_pipeline": "v1",
        "provenance": "scan",
        "ai_estimated_fields": recipe.estimated_fields(),
        "ocr_confidence": recipe.ocr_confidence,
        "dish_history": recipe.dish_history,
        "source_curiosities": recipe.source_curiosities,
        "unclear_spans": recipe.unclear_spans,
        "original_transcription": recipe.original_transcription,
    }

    body: dict[str, Any] = {
        "name": recipe.name,
        "description": recipe.description or (recipe.dish_history or ""),
        "recipeIngredient": [{"note": i.text, "quantity": 0} for i in recipe.ingredients],
        "recipeInstructions": [{"text": s.text} for s in recipe.instructions],
        "recipeCategory": categories,
        "tags": tags,
        "notes": notes,
        "extras": {k: v for k, v in extras.items() if v not in (None, [], "")},
        "settings": {"disableAmount": True},
        **_times(recipe),
    }
    if recipe.recipe_yield.value:
        body["recipeYield"] = recipe.recipe_yield.value
    return body


class MealieClient:
    """Minimal Mealie API client (organizers + create + patch + image + asset upload)."""

    def __init__(self, settings: Settings) -> None:
        if not settings.mealie_api_token:
            raise RuntimeError(
                "RECIPE_MEALIE_API_TOKEN is not set. Mint a token in the Mealie UI and "
                "port-forward: kubectl -n prod port-forward svc/mealie 9000:9000"
            )
        self.settings = settings
        self.http = httpx.Client(
            base_url=settings.mealie_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.mealie_api_token}"},
            timeout=60.0,
        )

    # --- organizers (tags / categories): get-or-create, return {id,name,slug} ---
    def _ensure_organizer(self, kind: str, name: str) -> dict:
        slug = slugify(name)
        r = self.http.get(f"/api/organizers/{kind}", params={"search": name, "perPage": 100})
        r.raise_for_status()
        for item in r.json().get("items", []):
            if item.get("slug") == slug or item.get("name", "").lower() == name.lower():
                return {"id": item["id"], "name": item["name"], "slug": item["slug"]}
        c = self.http.post(f"/api/organizers/{kind}", json={"name": name})
        c.raise_for_status()
        item = c.json()
        return {"id": item["id"], "name": item["name"], "slug": item["slug"]}

    def ensure_tags(self, names: list[str]) -> list[dict]:
        return [self._ensure_organizer("tags", n) for n in dict.fromkeys(names)]

    def ensure_categories(self, names: list[str]) -> list[dict]:
        return [self._ensure_organizer("categories", n) for n in dict.fromkeys(names)]

    # --- recipe create / update / media ---
    def create_recipe(self, name: str) -> str:
        r = self.http.post("/api/recipes", json={"name": name})
        r.raise_for_status()
        return r.json() if isinstance(r.json(), str) else r.text.strip('"')

    def patch_recipe(self, slug: str, payload: dict) -> None:
        r = self.http.patch(f"/api/recipes/{slug}", json=payload)
        r.raise_for_status()

    def set_image(self, slug: str, image_path: Path) -> None:
        ext = image_path.suffix.lstrip(".").lower() or "jpg"
        with image_path.open("rb") as fh:
            r = self.http.put(
                f"/api/recipes/{slug}/image",
                files={"image": (image_path.name, fh, "image/jpeg")},
                data={"extension": ext},
            )
        r.raise_for_status()

    def add_asset(self, slug: str, file_path: Path) -> None:
        ext = file_path.suffix.lstrip(".").lower() or "pdf"
        with file_path.open("rb") as fh:
            r = self.http.post(
                f"/api/recipes/{slug}/assets",
                data={"name": file_path.name, "icon": "mdi-file", "extension": ext},
                files={"file": (file_path.name, fh, "application/octet-stream")},
            )
        r.raise_for_status()

    def upload(self, recipe: Recipe, *, source_pdf: Path, page_one_image: Path | None) -> str:
        """Create + PATCH the recipe, set the image, attach the PDF. Return the new slug."""
        tags = self.ensure_tags([*recipe.tags, *self.settings.group_tag_list])
        categories = self.ensure_categories(recipe.categories)
        slug = self.create_recipe(recipe.name)
        self.patch_recipe(slug, recipe_to_payload(recipe, tags=tags, categories=categories))
        if page_one_image and page_one_image.exists():
            self.set_image(slug, page_one_image)
        if source_pdf.exists():
            self.add_asset(slug, source_pdf)
        return slug

    def close(self) -> None:
        self.http.close()


def upload_reviewed(settings: Settings, *, include_needs_review: bool = True) -> None:
    """Batch-upload reviewed recipes (status ok [+ needs_review]) from the manifest to Mealie."""
    manifest_path = settings.outbox / "_manifest.json"
    if not manifest_path.exists():
        logger.info("No manifest at %s — run `extract` first.", manifest_path)
        return
    manifest = json.loads(manifest_path.read_text())
    wanted = {"ok", "needs_review"} if include_needs_review else {"ok"}
    todo = [e for e in manifest if e.get("status") in wanted and not e.get("uploaded")]
    if not todo:
        logger.info("Nothing to upload (no un-uploaded %s recipes).", " / ".join(sorted(wanted)))
        return

    client = MealieClient(settings)
    logger.info("Uploading %d recipe(s) to %s", len(todo), settings.mealie_url)
    by_slug = {e["slug"]: e for e in manifest}
    try:
        for i, e in enumerate(todo, 1):
            out_dir = settings.outbox / e["slug"]
            recipe = _load_recipe(out_dir / "recipe.json")
            page1 = next(iter(sorted((out_dir / "pages").glob("page-*.jpg"))), None)
            pdf_path = out_dir / e["source"]
            try:
                new_slug = client.upload(recipe, source_pdf=pdf_path, page_one_image=page1)
                by_slug[e["slug"]]["uploaded"] = new_slug
                logger.info("[%d/%d] %s -> mealie/%s", i, len(todo), e["slug"], new_slug)
            except Exception as exc:  # noqa: BLE001 - isolate so the batch continues
                logger.exception("[%d/%d] FAILED %s: %s", i, len(todo), e["slug"], exc)
    finally:
        client.close()
    manifest_path.write_text(json.dumps(list(by_slug.values()), indent=2))
    logger.info("Upload complete. Filter in Mealie by tag: %s", ", ".join(settings.group_tag_list))


def _load_recipe(path: Path) -> Recipe:
    from recipe_pipeline.schema import Recipe

    return Recipe.model_validate_json(path.read_text())
