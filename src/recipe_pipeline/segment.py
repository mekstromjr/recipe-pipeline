"""Line/layout segmentation: a page image -> ordered text-line images.

Specialized HTR engines (TrOCR, Kraken) recognize one text line at a time, so a page must be
split into lines first. Options: a CRAFT detector (pairs with TrOCR) or Kraken's built-in
`blla` baseline segmenter. The choice is tied to the OCR backend picked in the bake-off.

STUB.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image


def segment_lines(image: Image.Image) -> list[Image.Image]:
    """Return ordered line-image crops for ``image`` (reading order: top-to-bottom)."""
    raise NotImplementedError(
        "Line segmentation not implemented yet (CRAFT for TrOCR, or Kraken `blla`). "
        "Scheduled for the OCR bake-off phase."
    )
