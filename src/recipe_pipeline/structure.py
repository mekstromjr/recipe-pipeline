"""Turn reconciled transcription(s) into a structured :class:`~recipe_pipeline.schema.Recipe`.

Also applies the relevance filter for newspaper clippings (see
prompts.STRUCTURE_SYSTEM_PROMPT): recipe content only; keep+[UNCLEAR] when unsure rather than
silently dropping; quaint off-recipe text -> ``source_curiosities``. All produced fields are
``Provenance.FROM_CARD`` here — enrichment/estimates are added later, separately.

STUB.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings
    from recipe_pipeline.schema import Recipe


def structure(settings: Settings, *, reconciled_pages: list[str], source_pdf: str) -> Recipe:
    """Build a :class:`Recipe` from the per-page reconciled text (concatenated into one recipe)."""
    raise NotImplementedError(
        "Structure not implemented yet. Use prompts.STRUCTURE_SYSTEM_PROMPT to extract recipe "
        "fields + apply the clipping relevance filter; mark everything Provenance.FROM_CARD."
    )
