"""Reconcile two OCR transcripts against the original page image (local vision LLM).

This is the accuracy keystone: research shows image-grounded reconciliation of two
different-architecture OCR outputs reaches CER ~1.8% (68-72% better than either engine alone),
because the image anchors the model and suppresses the plausible-but-wrong substitutions a
text-only merge would make. Unresolved disagreements come back wrapped in [UNCLEAR: ...].
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from recipe_pipeline import ollama_client
from recipe_pipeline.prompts import RECONCILE_SYSTEM_PROMPT

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings


def _user_prompt(transcript_a: str, transcript_b: str, *, label_a: str, label_b: str) -> str:
    return (
        f"OCR transcript A (engine: {label_a}):\n{transcript_a}\n\n"
        f"OCR transcript B (engine: {label_b}):\n{transcript_b}\n\n"
        "Reconcile these against the page image per your instructions. "
        "Output only the reconciled transcription."
    )


def reconcile(
    settings: Settings,
    *,
    transcript_a: str,
    transcript_b: str,
    page_image: Path,
    label_a: str = "A",
    label_b: str = "B",
) -> str:
    """Return one reconciled transcription of the page, with [UNCLEAR: ...] on unresolved spans."""
    return ollama_client.chat(
        settings,
        system=RECONCILE_SYSTEM_PROMPT,
        user=_user_prompt(transcript_a, transcript_b, label_a=label_a, label_b=label_b),
        images=[page_image],
        model=settings.reconcile_model,
    ).strip()
