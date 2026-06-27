"""Discover scanned recipes. One PDF in ``inbox/`` == one recipe."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# slugify now lives in the shared package (worker + server both need it); re-exported here
# so existing `from recipe_pipeline.ingest import slugify` call sites keep working.
from recipe_pipeline_shared.slug import slugify

__all__ = ["RecipeJob", "discover", "slugify"]


@dataclass(frozen=True)
class RecipeJob:
    """A single recipe to process: its source PDF and a stable slug derived from the filename."""

    pdf_path: Path
    slug: str

    @property
    def source_name(self) -> str:
        return self.pdf_path.name


def discover(inbox: Path) -> list[RecipeJob]:
    """Return one :class:`RecipeJob` per ``*.pdf`` in ``inbox`` (non-recursive), sorted by name."""
    if not inbox.exists():
        return []
    pdfs = sorted(
        (p for p in inbox.iterdir() if p.suffix.lower() == ".pdf" and p.is_file()),
        key=lambda p: p.name.lower(),
    )
    return [RecipeJob(pdf_path=p, slug=slugify(p.stem)) for p in pdfs]
