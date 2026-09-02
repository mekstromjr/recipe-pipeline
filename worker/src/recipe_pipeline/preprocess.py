"""Produce an upright page image for display.

**This module no longer participates in OCR.** Under Apple Vision it carried a whole
orientation-detection apparatus — a char-count comparison to spot 90/270, plus an LLM A/B
check on OCR line order to tell 0 from 180, because Vision was orientation-invariant and
returned a 180 page bottom-to-top. All of it is gone. Measured on glm-ocr:

  * A 180-rotated page transcribes correctly AND in upright line order, so there is nothing
    to detect and no line order to reverse.
  * A sideways page reads fine as scanned, and pre-rotating it to +90/+270 made the
    transcription measurably WORSE.

So the pipeline OCRs the raw scan, full stop. What remains here is fine-deskew for the
review thumbnail and the Mealie display image — a cosmetic concern, not a fidelity one.

The original PDF asset is never modified.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from deskew import determine_skew

if TYPE_CHECKING:
    from PIL import Image

MAX_DESKEW_DEG = 15.0  # ignore implausibly large 'skew' (likely a detection error)


def make_upright(image: Image.Image) -> Image.Image:
    """Fine-deskew a page for display. Never used before OCR."""
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    angle = determine_skew(gray)
    if angle is not None and 0.5 < abs(angle) <= MAX_DESKEW_DEG:
        return image.rotate(angle, expand=True, fillcolor=(255, 255, 255))
    return image
