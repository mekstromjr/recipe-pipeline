"""Thin client for the local Ollama server (reconcile + enrich both use it).

The reconcile stage REQUIRES a vision-capable model: it must see the page image, not just two
text strings, or it is merely guessing between two guesses. Verify ``settings.reconcile_model``
accepts ``images`` via Ollama's chat API (see ``verify_vision``); if not, switch to a Qwen2.5-VL
tag.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings


def chat(
    settings: Settings,
    *,
    system: str | None = None,
    user: str,
    images: list[Path] | None = None,
    model: str | None = None,
    format: dict | None = None,
    timeout: float = 600.0,
) -> str:
    """Send a chat turn to Ollama (`POST {ollama_host}/api/chat`) and return the reply text.

    ``images`` are base64-encoded and attached to the user message for vision models. ``format``
    is an optional JSON schema that constrains the reply to valid matching JSON (structured
    output). Temperature is pinned to 0 for deterministic, faithful output.
    """
    model = model or settings.reconcile_model
    user_msg: dict = {"role": "user", "content": user}
    if images:
        user_msg["images"] = [
            base64.b64encode(Path(p).read_bytes()).decode("ascii") for p in images
        ]
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append(user_msg)

    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        # num_ctx must be set explicitly: Ollama's small default silently truncates long prompts,
        # which would drop the system rules or the start of the OCR transcripts.
        "options": {"temperature": 0, "num_ctx": settings.ollama_num_ctx},
    }
    if format is not None:
        payload["format"] = format
    resp = httpx.post(f"{settings.ollama_host}/api/chat", json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def verify_vision(settings: Settings, sample_image: Path, *, model: str | None = None) -> str:
    """Smoke-test that the model accepts an image. Returns its reply (should describe the image)."""
    return chat(
        settings,
        system="You read images.",
        user="In one short sentence, describe what this image is.",
        images=[sample_image],
        model=model,
    )
