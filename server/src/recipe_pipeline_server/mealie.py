"""Async Mealie client for in-cluster injection (approve -> Mealie, no laptop involved).

Port of the worker's validated client (same call sequence and payload gotchas — see
``recipe_pipeline_shared.mealie_payload``), reshaped for the server: async httpx, and media
arrives as bytes pulled from Garage rather than local files.
"""

from __future__ import annotations

import httpx
from recipe_pipeline_shared.mealie_payload import recipe_to_payload
from recipe_pipeline_shared.schema import Recipe
from recipe_pipeline_shared.slug import slugify

from recipe_pipeline_server.config import Settings


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
                for n in dict.fromkeys([*recipe.tags, *self.settings.group_tag_list])
            ]
            categories = [
                await self._ensure_organizer("categories", n)
                for n in dict.fromkeys(recipe.categories)
            ]
            slug = await self.create_recipe(recipe.name)
            await self.patch_recipe(
                slug, recipe_to_payload(recipe, tags=tags, categories=categories)
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
