"""Pluggable OCR/HTR backends.

Two *different-architecture* backends transcribe each page independently; their disagreement is
the signal the reconcile stage uses. The concrete backend is chosen empirically by the bake-off.
"""

from recipe_pipeline.htr.base import HTRBackend, OcrLine, get_backend

__all__ = ["HTRBackend", "OcrLine", "get_backend"]
