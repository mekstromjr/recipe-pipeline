"""Discover scanned recipes. One PDF in ``inbox/`` == one recipe."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RecipeJob:
    """A single recipe to process: its source PDF and a stable slug derived from the filename."""

    pdf_path: Path
    slug: str

    @property
    def source_name(self) -> str:
        return self.pdf_path.name


def slugify(value: str) -> str:
    """ASCII, lowercase, hyphen-separated slug (matches the style Mealie uses for recipe slugs)."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    value = re.sub(r"[\s_-]+", "-", value)
    return value or "recipe"


def discover(inbox: Path) -> list[RecipeJob]:
    """Return one :class:`RecipeJob` per ``*.pdf`` in ``inbox`` (non-recursive), sorted by name."""
    if not inbox.exists():
        return []
    pdfs = sorted(
        (p for p in inbox.iterdir() if p.suffix.lower() == ".pdf" and p.is_file()),
        key=lambda p: p.name.lower(),
    )
    return [RecipeJob(pdf_path=p, slug=slugify(p.stem)) for p in pdfs]
