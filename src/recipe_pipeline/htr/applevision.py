"""Apple Vision backend — macOS on-device OCR (VNRecognizeTextRequest).

Optional cheap third engine: zero cost, fully on-device, returns per-candidate confidence, and
handles some orientation itself. Useful as a tie-breaker in the consensus step. macOS only.

STUB: implement via the Vision framework through `pyobjc` (VNRecognizeTextRequest with
`recognitionLevel = accurate`), or shell out to a small Swift helper.
"""

from __future__ import annotations

from recipe_pipeline.htr.base import HTRBackend


def build() -> HTRBackend:
    raise NotImplementedError(
        "Apple Vision backend not implemented yet. Implement via pyobjc VNRecognizeTextRequest "
        "(macOS only). Optional third engine for the bake-off."
    )
