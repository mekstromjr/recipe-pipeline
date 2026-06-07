"""Reconcile two OCR transcripts against the original page image (local vision LLM).

This is the accuracy keystone: research shows image-grounded reconciliation of two
different-architecture OCR outputs reaches CER ~1.8% (68-72% better than either engine alone),
because the image anchors the model and suppresses the plausible-but-wrong substitutions a
text-only merge would make. Unresolved disagreements come back wrapped in [UNCLEAR: ...].

STUB.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings


def reconcile(
    settings: Settings,
    *,
    transcript_a: str,
    transcript_b: str,
    page_image: Path,
) -> str:
    """Return one reconciled transcription of the page, with [UNCLEAR: ...] on unresolved spans."""
    raise NotImplementedError(
        "Reconcile not implemented yet. Use prompts.RECONCILE_SYSTEM_PROMPT via "
        "ollama_client.chat() with the page image attached."
    )
