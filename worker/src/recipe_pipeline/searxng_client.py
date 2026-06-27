"""SearXNG JSON-API client for metadata enrichment.

SearXNG's public ingress is behind Authentik; for programmatic access, port-forward the
in-cluster service (``kubectl -n prod port-forward svc/searxng 8888:8080``) and point
``settings.searxng_url`` at it. JSON output is enabled in the SearXNG config.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings


def search(settings: Settings, query: str, *, limit: int = 5, timeout: float = 20.0) -> list[dict]:
    """Return search results (`GET {searxng_url}/search?q=...&format=json`) as result dicts.

    Raises on connection/HTTP errors — callers (enrichment) treat that as best-effort and skip.
    """
    resp = httpx.get(
        f"{settings.searxng_url}/search",
        params={"q": query, "format": "json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])[:limit]
