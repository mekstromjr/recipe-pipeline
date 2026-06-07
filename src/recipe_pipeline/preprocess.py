"""Orientation + deskew, applied to rendered page images BEFORE OCR.

Two separate problems:
- Gross rotation (90/180/270 — page scanned sideways/upside-down): detected by trying all four
  rotations through the primary OCR engine and keeping the one with the most high-confidence
  text. (A vision LLM proved unreliable here — it failed to report a clear 180° flip.)
- Fine skew (a few degrees of tilt): corrected with the classical-CV `deskew` library.

The original PDF asset is never modified — correction happens only on working images.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from deskew import determine_skew
from PIL import Image

from recipe_pipeline.htr import get_backend

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings
    from recipe_pipeline.htr import HTRBackend

MAX_DESKEW_DEG = 15.0  # ignore implausibly large 'skew' (likely a detection error)
ROTATIONS = (0, 90, 180, 270)


def _rotate_cw(image: Image.Image, deg: int) -> Image.Image:
    # PIL rotates counter-clockwise for positive angles; negate for a clockwise rotation.
    return image if deg == 0 else image.rotate(-deg, expand=True, fillcolor=(255, 255, 255))


# A sideways orientation must beat upright by this margin in recognized-text volume to be
# trusted — keeps noise from rotating an already-upright page.
SIDEWAYS_MARGIN = 1.2


def _ocr_chars(backend: HTRBackend, image: Image.Image) -> int:
    """Amount of recognized text. Drops sharply for sideways (90/270) scans."""
    return len(backend.transcribe_page(image).text.strip())


def detect_rotation(settings: Settings, image: Image.Image) -> int:
    """Return a clockwise rotation to upright the page — conservatively.

    Apple Vision reads 0 and 180 equally well, so the 0-vs-180 flip is undetectable here and we
    default to 0 (most scans are upright). Sideways 90/270 scans recognize much worse, so we only
    rotate to one of those when it clearly beats upright. (180 detection is a known gap.)
    """
    backend = get_backend(settings.ocr_a)
    small = image.convert("RGB")
    small.thumbnail((1200, 1200))
    upright = _ocr_chars(backend, small)
    best_deg, best_chars = 0, upright
    for deg in (90, 270):
        chars = _ocr_chars(backend, _rotate_cw(small, deg))
        if chars > best_chars:
            best_deg, best_chars = deg, chars
    # Only accept a sideways rotation if it clearly beats upright.
    if best_deg in (90, 270) and best_chars < upright * SIDEWAYS_MARGIN:
        return 0
    return best_deg


def orient_and_deskew(image: Image.Image, settings: Settings) -> Image.Image:
    """Return an upright, deskewed copy of ``image`` ready for segmentation/OCR."""
    deg = detect_rotation(settings, image)
    if deg:
        image = _rotate_cw(image, deg)

    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    angle = determine_skew(gray)
    if angle is not None and 0.5 < abs(angle) <= MAX_DESKEW_DEG:
        image = image.rotate(angle, expand=True, fillcolor=(255, 255, 255))
    return image
