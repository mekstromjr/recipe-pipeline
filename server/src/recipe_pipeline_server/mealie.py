"""Async Mealie client for in-cluster injection (approve -> Mealie, no laptop involved).

Port of the worker's validated client (same call sequence and payload gotchas — see
``recipe_pipeline_shared.mealie_payload``), reshaped for the server: async httpx, and media
arrives as bytes pulled from Garage rather than local files.
"""

from __future__ import annotations

import logging

import httpx
from recipe_pipeline_shared.mealie_payload import model_tags, recipe_to_payload
from recipe_pipeline_shared.schema import Recipe
from recipe_pipeline_shared.slug import slugify

from recipe_pipeline_server.config import Settings

logger = logging.getLogger("recipe_service")


def _plain_ingredient(text: str) -> dict:
    """The unparsed fallback: the whole string as a note, amounts off for this row."""
    return {"quantity": 0, "note": text, "originalText": text, "disableAmount": True}


class MealieError(RuntimeError):
    """Injection failed; message is safe to store on the job row."""


class MealieClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        if not settings.mealie_api_token:
            raise MealieError("mealie_api_token is not configured")
        self.settings = settings
        self.http = httpx.AsyncClient(
            base_url=settings.mealie_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.mealie_api_token}"},
            timeout=60.0,
            transport=transport,
        )

    # --- organizers (tags / categories): get-or-create, return {id,name,slug} ---
    async def _ensure_organizer(self, kind: str, name: str) -> dict:
        slug = slugify(name)
        r = await self.http.get(f"/api/organizers/{kind}", params={"search": name, "perPage": 100})
        r.raise_for_status()
        for item in r.json().get("items", []):
            if item.get("slug") == slug or item.get("name", "").lower() == name.lower():
                return {"id": item["id"], "name": item["name"], "slug": item["slug"]}
        c = await self.http.post(f"/api/organizers/{kind}", json={"name": name})
        c.raise_for_status()
        item = c.json()
        return {"id": item["id"], "name": item["name"], "slug": item["slug"]}

    async def _ensure_food(self, name: str) -> dict | None:
        """Look up or create a Mealie food, returning ``{id, name}``.

        A ``food`` with ``id: null`` makes Mealie return HTTP 500 (measured), so a parsed
        food MUST be resolved to a real row before it can be referenced. Creating a duplicate
        name is a 400 (a unique constraint), hence look-up-then-create rather than create-then
        -handle. Returns None if the food cannot be resolved, so the caller falls back to a
        plain note rather than sending a payload that 500s.
        """
        name = (name or "").strip()
        if not name:
            return None
        try:
            r = await self.http.get("/api/foods", params={"search": name, "perPage": 100})
            r.raise_for_status()
            for item in r.json().get("items", []):
                if item.get("name", "").strip().lower() == name.lower():
                    return {"id": item["id"], "name": item["name"]}
            c = await self.http.post("/api/foods", json={"name": name})
            c.raise_for_status()
            item = c.json()
            return {"id": item["id"], "name": item["name"]}
        except httpx.HTTPError:
            logger.warning("could not resolve mealie food %r; falling back to note", name)
            return None

    async def parse_ingredients(self, texts: list[str]) -> list[dict]:
        """Turn ingredient strings into Mealie ingredient objects using Mealie's own parser.

        ``originalText`` is ALWAYS the card's verbatim string. The parser normalises wording —
        measured: "20 large marshmellows" becomes the food "marshmallow", and "1 8 oz pkg.
        Pecan nuts" parses to the food "pkg." while "Pecan nuts" drops into the note. This
        pipeline's promise is to preserve what the card said, so the original travels with
        every row whether the parse was good or not.

        Below ``ingredient_parse_min_confidence`` the parse is discarded and the row falls back
        to a note holding the whole string — the pre-parsing behaviour, which is never wrong,
        only unstructured.
        """
        if not texts:
            return []
        try:
            r = await self.http.post(
                "/api/parser/ingredients", json={"parser": "nlp", "ingredients": texts}
            )
            r.raise_for_status()
            parsed = r.json()
        except httpx.HTTPError as exc:
            logger.warning("mealie ingredient parser unavailable (%s); sending plain notes", exc)
            return [_plain_ingredient(t) for t in texts]

        out: list[dict] = []
        for text, item in zip(texts, parsed, strict=False):
            confidence = (item.get("confidence") or {}).get("average") or 0.0
            ing = item.get("ingredient") or {}
            if confidence < self.settings.ingredient_parse_min_confidence:
                out.append(_plain_ingredient(text))
                continue
            food = ing.get("food") or {}
            resolved = await self._ensure_food(food.get("name", "")) if food.get("name") else None
            unit = ing.get("unit") or None
            if unit and not unit.get("id"):
                unit = None  # same 500 risk as an unsaved food
            out.append(
                {
                    "quantity": ing.get("quantity") or 0,
                    "unit": unit,
                    "food": resolved,
                    "note": ing.get("note") or "",
                    "originalText": text,
                    "disableAmount": False,
                }
            )
        return out

    async def create_recipe(self, name: str) -> str:
        r = await self.http.post("/api/recipes", json={"name": name})
        r.raise_for_status()
        body = r.json()
        return body if isinstance(body, str) else r.text.strip('"')

    async def patch_recipe(self, slug: str, payload: dict) -> None:
        r = await self.http.patch(f"/api/recipes/{slug}", json=payload)
        r.raise_for_status()

    async def set_image(self, slug: str, image: bytes) -> None:
        r = await self.http.put(
            f"/api/recipes/{slug}/image",
            files={"image": ("page-1.jpg", image, "image/jpeg")},
            data={"extension": "jpg"},
        )
        r.raise_for_status()

    async def add_asset(self, slug: str, filename: str, blob: bytes) -> None:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "pdf"
        r = await self.http.post(
            f"/api/recipes/{slug}/assets",
            data={"name": filename, "icon": "mdi-file", "extension": ext},
            files={"file": (filename, blob, "application/octet-stream")},
        )
        r.raise_for_status()

    async def upload(
        self,
        recipe: Recipe,
        *,
        source: tuple[str, bytes] | None = None,
        page_one_image: bytes | None = None,
    ) -> str:
        """Create + PATCH the recipe, set the image, attach the source. Return the new slug."""
        try:
            tags = [
                await self._ensure_organizer("tags", n)
                for n in dict.fromkeys(
                    [*recipe.tags, *model_tags(recipe), *self.settings.group_tag_list]
                )
            ]
            categories = [
                await self._ensure_organizer("categories", n)
                for n in dict.fromkeys(recipe.categories)
            ]
            slug = await self.create_recipe(recipe.name)
            # Parse through Mealie's own parser so amounts/units/foods are structured rather
            # than one opaque note per line, which is what forced a manual edit -> parse ->
            # select in the UI for every imported recipe.
            ingredients = await self.parse_ingredients([i.text for i in recipe.ingredients])
            await self.patch_recipe(
                slug,
                recipe_to_payload(
                    recipe, tags=tags, categories=categories, ingredients=ingredients
                ),
            )
            if page_one_image:
                await self.set_image(slug, page_one_image)
            if source:
                await self.add_asset(slug, source[0], source[1])
        except httpx.HTTPError as exc:
            raise MealieError(f"mealie injection failed: {exc}") from exc
        return slug

    async def close(self) -> None:
        await self.http.aclose()
