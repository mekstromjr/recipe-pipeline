"""Orientation + deskew, applied to rendered page images BEFORE OCR.

Two separate problems:
- Gross rotation (90/180/270 — page scanned sideways): detect via the local vision model
  ("what rotation makes this readable?"), which is robust on handwriting where print-oriented
  detectors (e.g. Tesseract OSD) struggle. Then rotate.
- Fine skew (a few degrees of tilt): correct with the classical-CV `deskew` library.

The original PDF asset is never modified — correction happens only on working images.
The exact gross-orientation detector is validated during the bake-off.

STUB.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

    from recipe_pipeline.config import Settings


def orient_and_deskew(image: Image.Image, settings: Settings) -> Image.Image:
    """Return an upright, deskewed copy of ``image`` ready for segmentation/OCR."""
    raise NotImplementedError(
        "Orientation/deskew not implemented yet. Gross rotation via the vision model "
        "(settings.reconcile_model), fine skew via the `deskew` lib."
    )
