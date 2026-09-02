"""glm-ocr backend — the production OCR engine, on the CPU Ollama in <ct>.

Replaces Apple Vision. Measured against the ground-truth files in ``samples/``:

  * Clean cursive (``01-jello``) matched the Apple Vision transcription line for line.
  * A harder card (``02-cheese-rotated``) produced real errors — "Worchestershire sauce"
    read as "shredded cheese". Manual review is the gate for these; no automated check
    catches a plausible substitution.
  * **Orientation-invariant, and better than Vision at it**: a 180-rotated page transcribes
    correctly AND in upright line order, so there is no line-reversal step. Pre-rotating a
    sideways page to +90/+270 made the result WORSE, which is why ``preprocess`` no longer
    rotates before OCR.

Two failure modes are specific to this model and are gated in ``review``:
repetition (the whole transcript repeated 2-3x) and runaway generation on faint input.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from recipe_pipeline import ollama_client
from recipe_pipeline.htr.base import OcrLine, PageResult

if TYPE_CHECKING:
    from PIL import Image

    from recipe_pipeline.config import Settings

# Non-empty and explicit: glm-ocr returns "" for an empty prompt. "Exactly as written" matters
# on this corpus — the value of OCR here is fidelity to the card, not readable prose.
PROMPT = (
    "Transcribe all text in this image exactly as written. Preserve line breaks and "
    "reading order. Output only the transcription, with no commentary."
)

# glm-ocr emits LaTeX for ordinary typographic glyphs: a "1/2" on the card comes back as
# "$\frac{1}{2}$". Recipes are MADE of fractions, so without this every quantity is corrupt.
_FRAC = re.compile(r"\$?\\[dt]?frac\{([^{}]+)\}\{([^{}]+)\}\$?")
# Strip $...$ left around plain content ("$2$" -> "2"). Short spans only — a genuine equation
# on a recipe card is vanishingly unlikely, but the bound costs nothing.
_BARE_MATH = re.compile(r"\$([^$\n]{1,40})\$")


def normalize_latex(text: str) -> str:
    """Undo the LaTeX-isms glm-ocr introduces for ordinary glyphs (mainly fractions)."""
    text = _FRAC.sub(r"\1/\2", text)
    return _BARE_MATH.sub(r"\1", text)


def downscale(image: Image.Image, max_edge: int) -> Image.Image:
    """Cap the longest edge. Full-resolution renders cost real CPU in the vision encoder for
    no accuracy gain at this model size."""
    from PIL import Image as _Image

    if max(image.size) <= max_edge:
        return image
    ratio = max_edge / max(image.size)
    new = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    return image.resize(new, _Image.LANCZOS)


class GlmOcrBackend:
    """Transcribe a page with glm-ocr via Ollama's /api/generate."""

    name = "glmocr"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def transcribe_page(self, image: Image.Image) -> PageResult:
        prepared = downscale(image.convert("RGB"), self._settings.ocr_max_edge)
        raw, _seconds = ollama_client.generate_with_image(self._settings, PROMPT, prepared)
        text = normalize_latex(raw).strip()
        # glm-ocr returns a flat transcript, not per-line geometry, so there are no bboxes or
        # confidences to report. Split on newlines so downstream line handling still works.
        lines = [OcrLine(text=ln) for ln in text.splitlines() if ln.strip()]
        return PageResult(text=text, lines=lines)


def build(settings: Settings) -> GlmOcrBackend:
    return GlmOcrBackend(settings)
