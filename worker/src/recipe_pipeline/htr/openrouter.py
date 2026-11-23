"""OpenRouter cloud-vision OCR backend — the escalation engine for hard cursive.

Apple Vision (local) handles the readable majority; cards it fails are escalated to a strong
cloud vision model (default Gemini 2.5 Flash) that reads difficult handwriting far better. This
is the ONLY stage that sends a scan off-device, and only for the failed tail.

Cloud VLMs can still fabricate on truly-illegible text, so escalated recipes still pass through
the same structure + review gate, and the verbatim transcription is preserved.

Requires ``RECIPE_OPENROUTER_API_KEY``.
"""

from __future__ import annotations

import base64
import io
from typing import TYPE_CHECKING

import httpx

from recipe_pipeline.config import load_settings
from recipe_pipeline.htr.base import HTRBackend, OcrLine, PageResult

if TYPE_CHECKING:
    from PIL import Image

    from recipe_pipeline.config import Settings

_PROMPT = (
    "Transcribe this scanned recipe card exactly as written, line by line, top to bottom. "
    "Preserve the original wording and any misspellings. If a word is truly illegible, write "
    "[UNCLEAR]. Output only the transcription, no commentary."
)


class OpenRouterVisionBackend:
    name = "openrouter"

    def __init__(self, settings: Settings) -> None:
        if not settings.openrouter_api_key:
            raise NotImplementedError(
                "OpenRouter backend needs RECIPE_OPENROUTER_API_KEY "
                "(set it in recipe-pipeline/.env)."
            )
        self.settings = settings

    def transcribe_page(self, image: Image.Image) -> PageResult:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, "JPEG", quality=90)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.settings.openrouter_api_key}"},
            json={
                "model": self.settings.openrouter_ocr_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                            },
                        ],
                    }
                ],
            },
            timeout=180.0,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        # Cloud returns whole-page text (no per-line boxes); keep one line per text line.
        return PageResult(text=text, lines=[OcrLine(text=ln) for ln in text.splitlines() if ln])


def build(settings: Settings | None = None) -> HTRBackend:
    return OpenRouterVisionBackend(settings or load_settings())
