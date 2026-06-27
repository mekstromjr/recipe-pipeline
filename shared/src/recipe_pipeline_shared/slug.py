"""Slug derivation shared by the worker (job slugs) and server (Mealie organizer slugs)."""

from __future__ import annotations

import re
import unicodedata


def slugify(value: str) -> str:
    """ASCII, lowercase, hyphen-separated slug (matches the style Mealie uses for recipe slugs)."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    value = re.sub(r"[\s_-]+", "-", value)
    return value or "recipe"
