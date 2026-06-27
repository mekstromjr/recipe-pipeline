"""Orientation detection + producing an upright image.

Upside-down scans are common in this corpus. Key fact learned empirically: **Apple Vision reads
the ORIGINAL scan best** (it auto-handles orientation internally); pre-rotating the pixels
actually DEGRADES its recognition. So we do NOT rotate before OCR — we OCR the raw page and, for
a 180 page, just reverse the line order (Vision returns an upside-down page bottom-to-top). We
produce an upright image only for the things that genuinely need it: the vision review pass
(gemma hallucinates on rotated images) and the Mealie display thumbnail.

Detection:
  * Sideways (90/270): Apple Vision recognizes far less text, so a clear char-count win signals it.
  * Upside-down (180): Vision is orientation-INVARIANT, so char-count can't tell 0 from 180.
    Instead we OCR at 0 and 180 and ask the local text model which line-order reads as a coherent
    recipe (title/ingredients first, instructions last) — a forced A/B choice it answers reliably.

The original PDF asset is never modified.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from deskew import determine_skew
from PIL import Image

from recipe_pipeline import ollama_client
from recipe_pipeline.htr import get_backend

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings
    from recipe_pipeline.htr import HTRBackend

MAX_DESKEW_DEG = 15.0  # ignore implausibly large 'skew' (likely a detection error)
SIDEWAYS_MARGIN = 1.2  # a 90/270 rotation must beat upright by this in text volume to be trusted

_ORIENT_AB_PROMPT = (
    "Two transcriptions of the SAME recipe card, each read top-to-bottom. One scan was upright, "
    "the other upside-down (so its lines come out in reverse order). A correct recipe begins with "
    "a TITLE and ingredients and ends with instructions/serving notes. Which version is the "
    "upright one? Answer ONLY 1 or 2."
)


def _rotate_cw(image: Image.Image, deg: int) -> Image.Image:
    # PIL rotates counter-clockwise for positive angles; negate for a clockwise rotation.
    return image if deg == 0 else image.rotate(-deg, expand=True, fillcolor=(255, 255, 255))


def _chars(backend: HTRBackend, image: Image.Image) -> int:
    return len(backend.transcribe_page(image).text.strip())


def _is_upside_down(settings: Settings, backend: HTRBackend, image: Image.Image) -> bool:
    """Decide 0 vs 180 by asking the text model which OCR line-order is a coherent recipe."""
    text0 = backend.transcribe_page(image).text
    text180 = backend.transcribe_page(_rotate_cw(image, 180)).text
    if len(text0.strip()) < 12 and len(text180.strip()) < 12:
        return False  # nothing to judge
    reply = ollama_client.chat(
        settings,
        user=f"{_ORIENT_AB_PROMPT}\n\n--- Version 1 ---\n{text0}\n\n--- Version 2 ---\n{text180}",
        model=settings.enrich_model,  # text-only judgment
    ).strip()
    return reply.startswith("2")


def detect_rotation(settings: Settings, image: Image.Image) -> int:
    """Return the clockwise rotation (0/90/180/270) that makes the page upright."""
    backend = get_backend(settings.ocr_a)
    small = image.convert("RGB")
    small.thumbnail((1200, 1200))

    upright = _chars(backend, small)
    side_deg, side_chars = 0, upright
    for deg in (90, 270):
        c = _chars(backend, _rotate_cw(small, deg))
        if c > side_chars:
            side_deg, side_chars = deg, c
    # Clear sideways win -> trust it (sideways recognizes much worse, so the margin is real).
    if side_deg in (90, 270) and side_chars >= upright * SIDEWAYS_MARGIN:
        return side_deg
    # Otherwise it's 0 or 180 — Apple Vision can't tell, so use the A/B text check.
    return 180 if _is_upside_down(settings, backend, small) else 0


def make_upright(image: Image.Image, deg: int) -> Image.Image:
    """Rotate by ``deg`` (clockwise) + fine-deskew, for the review pass and display thumbnail.

    NOT used before OCR — Apple Vision reads the raw scan better than a pre-rotated one.
    """
    if deg:
        image = _rotate_cw(image, deg)
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    angle = determine_skew(gray)
    if angle is not None and 0.5 < abs(angle) <= MAX_DESKEW_DEG:
        image = image.rotate(angle, expand=True, fillcolor=(255, 255, 255))
    return image
