"""TrOCR backend (Microsoft ``trocr-large-handwritten``) — transformer architecture.

Best zero-shot CER on modern handwriting among local engines; MPS-accelerated. Operates on
line images, so it pairs with a separate line detector in the segmentation stage. Confidence is
sequence-level only (treat as a coarse hint, not a gate).

STUB: implement with `transformers` (install the `ml` extra). Load
``microsoft/trocr-large-handwritten`` on ``mps`` and run the processor + generate loop.
"""

from __future__ import annotations

from recipe_pipeline.htr.base import HTRBackend


def build() -> HTRBackend:
    raise NotImplementedError(
        "TrOCR backend not implemented yet. Install the `ml` extra and load "
        "microsoft/trocr-large-handwritten on MPS. Scheduled for the OCR bake-off phase."
    )
