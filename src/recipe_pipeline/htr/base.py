"""HTR backend protocol + registry.

A backend takes line images (from the segmentation stage) and returns transcribed text, ideally
with a confidence score and bounding box. Confidence is optional because not every engine
exposes it (TrOCR's is unreliable; Kraken's is per-character and solid) — the reconcile stage
does not depend on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from PIL import Image


@dataclass
class OcrLine:
    """One transcribed text line."""

    text: str
    confidence: float | None = None
    bbox: tuple[int, int, int, int] | None = None  # (x0, y0, x1, y1) in page pixels


@runtime_checkable
class HTRBackend(Protocol):
    """Interface every OCR backend implements."""

    name: str

    def transcribe_lines(self, line_images: list[Image.Image]) -> list[OcrLine]:
        """Transcribe pre-segmented line images, in order."""
        ...


# Backend identifier -> dotted import path of a factory `build() -> HTRBackend`.
_REGISTRY: dict[str, str] = {
    "trocr": "recipe_pipeline.htr.trocr:build",
    "kraken": "recipe_pipeline.htr.kraken:build",
    "applevision": "recipe_pipeline.htr.applevision:build",
}


def get_backend(name: str) -> HTRBackend:
    """Instantiate a backend by name. Raises ``KeyError`` for unknown names."""
    import importlib

    if name not in _REGISTRY:
        raise KeyError(f"Unknown OCR backend {name!r}. Known: {sorted(_REGISTRY)}")
    module_path, factory = _REGISTRY[name].split(":")
    mod = importlib.import_module(module_path)
    return getattr(mod, factory)()
