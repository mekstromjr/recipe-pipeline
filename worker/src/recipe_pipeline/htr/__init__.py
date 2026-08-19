"""Pluggable OCR/HTR backends. glm-ocr (on <pve-host>) is the production engine; the interface
stays pluggable for future engines."""

from recipe_pipeline.htr.base import (
    HTRBackend,
    OcrLine,
    PageResult,
    get_backend,
    known_backends,
)

__all__ = ["HTRBackend", "OcrLine", "PageResult", "get_backend", "known_backends"]
