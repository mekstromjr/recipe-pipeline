"""SearXNG JSON-API client for metadata enrichment.

SearXNG's public ingress is behind Authentik; for programmatic access, port-forward the
in-cluster service (``kubectl -n prod port-forward svc/searxng 8888:8080``) and point
``settings.searxng_url`` at it. JSON output is enabled in the SearXNG config.

STUB.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings


def search(settings: Settings, query: str, *, limit: int = 5) -> list[dict]:
    """Return search results (`GET {searxng_url}/search?q=...&format=json`) as result dicts."""
    raise NotImplementedError(
        "SearXNG client not implemented yet. GET {searxng_url}/search?format=json. "
        "Only the dish NAME is ever queried — never card quantities."
    )
