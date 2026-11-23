"""Client for the CPU Ollama host (`ollama.meklab.net`).

Ported back from `scribe`, whose own client header records that it was adapted FROM this
file — scribe then ran glm-ocr + qwen3.5:4b in production and learned things this pipeline
had not. Those lessons are folded in here:

  * glm-ocr is NOT a chat model. Its Ollama template is a bare ``{{ .Prompt }}``, so it must
    be driven through ``/api/generate``. ``/api/chat`` yields degenerate output — measured as
    a 4m42s run of "-" characters.
  * glm-ocr reports ZERO for every timing field it returns (load_duration, eval_count,
    total_duration). Wall clock must be measured client-side or you get a confident, wrong
    "0.0s".
  * ``num_thread`` must be sent on EVERY request. llama.cpp sizes its thread pool from host
    core count and ignores the cgroup quota; pinning it measured +73% generation throughput.
    There is no server-side setting for this.
  * qwen3.5 is a *thinking* model, so ``think`` is disabled for structured calls — its
    reasoning trace would be generated at CPU speed for output we discard.
"""

from __future__ import annotations

import base64
import io
import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from PIL import Image

    from recipe_pipeline.config import Settings


class OllamaError(RuntimeError):
    """Any failure talking to Ollama, with the host in the message.

    The host matters in the message: the most common misconfiguration is pointing at a
    laptop's own ollama instead of the shared host, which fails in confusing ways rather
    than loudly.
    """


def _options(settings: Settings, **extra: object) -> dict:
    """Options every request shares. temperature 0 — transcription must not vary run to run."""
    return {
        "temperature": 0,
        "num_ctx": settings.ollama_num_ctx,
        "num_thread": settings.num_thread,
        **extra,
    }


def generate_with_image(
    settings: Settings,
    prompt: str,
    image: Image.Image,
    *,
    model: str | None = None,
) -> tuple[str, float]:
    """Run a vision prompt against one page image. Returns (text, wall_clock_seconds).

    ``prompt`` must be non-empty: glm-ocr returns "" for an empty prompt rather than
    defaulting to a transcription instruction.
    """
    if not prompt.strip():
        raise ValueError("glm-ocr returns an empty string for an empty prompt")

    buf = io.BytesIO()
    image.convert("RGB").save(buf, "PNG")
    payload = {
        "model": model or settings.ocr_model,
        "prompt": prompt,
        "images": [base64.b64encode(buf.getvalue()).decode("ascii")],
        "stream": False,
        "options": _options(settings, num_predict=settings.ocr_num_predict),
    }
    t0 = time.monotonic()
    try:
        resp = httpx.post(
            f"{settings.ollama_host}/api/generate",
            json=payload,
            timeout=settings.ollama_timeout_seconds,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise OllamaError(f"OCR request failed against {settings.ollama_host}: {exc}") from exc
    return resp.json().get("response", ""), time.monotonic() - t0


def chat(
    settings: Settings,
    *,
    system: str | None = None,
    user: str,
    images: list[Image.Image] | None = None,
    model: str | None = None,
    format: dict | None = None,
) -> str:
    """Send a chat turn to the TEXT model and return the reply.

    ``format`` is an optional JSON schema Ollama enforces server-side, so the model cannot
    return prose that then has to be scraped.
    """
    user_msg: dict = {"role": "user", "content": user}
    if images:
        encoded = []
        for img in images:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "PNG")
            encoded.append(base64.b64encode(buf.getvalue()).decode("ascii"))
        user_msg["images"] = encoded

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append(user_msg)

    payload: dict = {
        "model": model or settings.text_model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": _options(settings),
    }
    if format is not None:
        payload["format"] = format

    try:
        resp = httpx.post(
            f"{settings.ollama_host}/api/chat",
            json=payload,
            timeout=settings.ollama_timeout_seconds,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise OllamaError(f"chat request failed against {settings.ollama_host}: {exc}") from exc
    return resp.json().get("message", {}).get("content", "")


def health(settings: Settings) -> list[str]:
    """Return the model names this host serves.

    An identity check, not just a liveness one: a dev Mac runs its own ollama, so a
    misconfigured host answers happily with entirely different models. Callers should confirm
    the expected models are present before trusting any result.
    """
    try:
        resp = httpx.get(f"{settings.ollama_host}/api/tags", timeout=10.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise OllamaError(f"cannot reach ollama at {settings.ollama_host}: {exc}") from exc
    return [m["name"] for m in resp.json().get("models", [])]
