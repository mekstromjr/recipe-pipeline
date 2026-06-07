"""Thin client for the local Ollama server (reconcile + enrich both use it).

The reconcile stage REQUIRES a vision-capable model: it must see the page image, not just two
text strings, or it is merely guessing between two guesses. Before relying on this, confirm
``settings.reconcile_model`` accepts ``images`` via Ollama's chat API; if not, switch to a
Qwen2.5-VL tag.

STUB.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings


def chat(
    settings: Settings,
    *,
    system: str,
    user: str,
    images: list[Path] | None = None,
    model: str | None = None,
) -> str:
    """Send a chat turn to Ollama (`POST {ollama_host}/api/chat`) and return the reply text.

    ``images`` are base64-encoded and attached to the user message for vision models.
    """
    raise NotImplementedError(
        "Ollama client not implemented yet. POST to {ollama_host}/api/chat with base64 images; "
        "verify the model accepts image input."
    )
