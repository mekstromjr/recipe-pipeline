"""Kraken backend — CNN-RNN/CTC architecture (digital-humanities HTR lineage).

Different architecture from TrOCR (the whole point of the consensus step). Ships native
per-character confidence and its own ``blla`` baseline segmenter, and has the best low-effort
fine-tuning loop (via eScriptorium) — the escalation path for a stubborn cursive hand. CPU-only
on Apple Silicon today (MPS training is unsupported upstream), which is fine for bursty use.

STUB: implement with the `kraken` package (install the `ml` extra) — load a handwriting model
via `kraken.lib.models`, run recognition, read `record.confidences`.
"""

from __future__ import annotations

from recipe_pipeline.htr.base import HTRBackend


def build() -> HTRBackend:
    raise NotImplementedError(
        "Kraken backend not implemented yet. Install the `ml` extra, fetch a handwriting model "
        "(`kraken get`), and run recognition. Scheduled for the OCR bake-off phase."
    )
