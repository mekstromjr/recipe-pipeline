"""Re-parse the ingredients of recipes already in Mealie (rerunnable).

Every recipe imported before ingredient parsing existed has each line stored as ONE opaque
note with ``quantity: 0`` and ``settings.disableAmount: True``, which is why a human had to
run edit -> parse -> select in the Mealie UI for each one. This walks those recipes and
rewrites their ingredients through Mealie's own parser, exactly as a fresh import now does.

    kubectl -n prod exec deploy/recipe-service -- \
        /app/.venv/bin/python -m recipe_pipeline_server.reparse --dry-run
    kubectl -n prod exec deploy/recipe-service -- \
        /app/.venv/bin/python -m recipe_pipeline_server.reparse --limit 5
    kubectl -n prod exec deploy/recipe-service -- \
        /app/.venv/bin/python -m recipe_pipeline_server.reparse

Deliberate properties:

* **Sequential.** Mealie here is backed by SQLite — a duplicate food surfaced a raw
  ``sqlite3.IntegrityError`` — so concurrent food creation would race on the unique
  constraint. There are ~190 recipes; this does not need to be fast.
* **Idempotent and skip-first.** A recipe whose ingredients already carry ``originalText`` has
  been parsed, and is left alone. Re-running only picks up what is still unparsed.
* **Never destructive.** ``originalText`` is set to the CARD's verbatim line, so a bad parse
  loses nothing: the original text survives on the row it came from. A recipe whose lines
  cannot be recovered is skipped rather than rewritten.
* **--dry-run reports without writing**, because this edits recipes a human has already
  approved.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import httpx

from recipe_pipeline_server.config import load_settings
from recipe_pipeline_server.mealie import MealieClient

logger = logging.getLogger("recipe_service.reparse")


def _needs_parse(recipe: dict) -> bool:
    """True if this recipe still has the pre-parsing shape.

    ``originalText`` is the marker: the parser always sets it and the old payload never did,
    so it distinguishes 'already parsed' from 'parsed but the parser found no food'.
    """
    rows = recipe.get("recipeIngredient") or []
    if not rows:
        return False
    return all(not r.get("originalText") for r in rows)


def _original_lines(recipe: dict) -> list[str]:
    """The ingredient text to re-parse: the note is where the whole line used to live."""
    out = []
    for r in recipe.get("recipeIngredient") or []:
        text = (r.get("originalText") or r.get("note") or "").strip()
        if text:
            out.append(text)
    return out


async def reparse_all(*, dry_run: bool, limit: int | None) -> int:
    settings = load_settings()
    changed = 0
    mealie = MealieClient(settings)
    try:
        page, per_page = 1, 50
        seen = 0
        while True:
            r = await mealie.http.get("/api/recipes", params={"page": page, "perPage": per_page})
            r.raise_for_status()
            items = r.json().get("items", [])
            if not items:
                break
            for item in items:
                slug = item.get("slug")
                if limit is not None and seen >= limit:
                    return changed
                detail = (await mealie.http.get(f"/api/recipes/{slug}")).json()
                if not _needs_parse(detail):
                    continue
                lines = _original_lines(detail)
                if not lines:
                    continue
                seen += 1
                if dry_run:
                    logger.info("would reparse %s (%d ingredients)", slug, len(lines))
                    changed += 1
                    continue
                try:
                    parsed = await mealie.parse_ingredients(lines)
                    await mealie.http.patch(
                        f"/api/recipes/{slug}",
                        json={
                            "recipeIngredient": parsed,
                            "settings": {**(detail.get("settings") or {}),
                                         "disableAmount": False},
                        },
                    )
                    structured = sum(1 for p in parsed if p.get("food"))
                    logger.info(
                        "reparsed %s: %d/%d structured", slug, structured, len(parsed)
                    )
                    changed += 1
                except httpx.HTTPError as exc:
                    # One bad recipe must not end the run; it stays in its old shape and the
                    # next run picks it up again.
                    logger.warning("skipped %s: %s", slug, exc)
            page += 1
    finally:
        await mealie.close()
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run", action="store_true", help="report what would change; write nothing"
    )
    ap.add_argument(
        "--limit", type=int, default=None, help="stop after N recipes (for a trial run)"
    )
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    n = asyncio.run(reparse_all(dry_run=args.dry_run, limit=args.limit))
    logger.info("%s %d recipe(s)", "would change" if args.dry_run else "changed", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
