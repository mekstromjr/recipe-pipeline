"""OCR bake-off harness — answers the cursive question empirically.

No published benchmark covers hard cursive recipe cards, so we measure on the real thing:
run each candidate OCR backend over sample PDFs in ``samples/`` that have a hand-typed
ground-truth ``.txt``, score CER/WER (jiwer), and report a ranking. Cloud engines may be
included purely as labeled reference ceilings. The winner becomes OCR_A/OCR_B; if zero-shot
cursive is too weak, the escalation is to fine-tune Kraken on the specific hand.

Sample layout:  samples/<name>.pdf  +  samples/<name>.txt  (ground truth)

STUB.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings


def run_bakeoff(settings: Settings, *, backends: list[str]) -> None:
    """Score each backend in ``backends`` against the ground-truth samples and print a ranking."""
    raise NotImplementedError(
        "Bake-off not implemented yet. Render samples, run each backend, score CER/WER with "
        "jiwer against samples/<name>.txt, print a ranking. This is the first build phase."
    )
