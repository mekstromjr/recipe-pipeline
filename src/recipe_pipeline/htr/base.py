"""OCR backend protocol + registry.

A backend transcribes a whole page image and returns the text plus, where available, per-line
detail (text, confidence, bbox). Apple Vision is the production engine (accurate, on-device,
orientation-tolerant); the registry keeps the interface pluggable for future engines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from PIL import Image


@dataclass
class OcrLine:
    """One transcribed text line."""

    text: str
    confidence: float | None = None
    bbox: tuple[float, float, float, float] | None = None  # normalized (x0, y0, x1, y1)


@dataclass
class PageResult:
    """A backend's transcription of one page."""

    text: str
    lines: list[OcrLine] = field(default_factory=list)

    @classmethod
    def from_lines(cls, lines: list[OcrLine]) -> PageResult:
        return cls(text="\n".join(line.text for line in lines), lines=lines)


@runtime_checkable
class HTRBackend(Protocol):
    """Interface every OCR backend implements."""

    name: str

    def transcribe_page(self, image: Image.Image) -> PageResult:
        """Transcribe a full page image (the backend handles its own line segmentation)."""
        ...


# Backend identifier -> dotted import path of a factory `build() -> HTRBackend`.
_REGISTRY: dict[str, str] = {
    "applevision": "recipe_pipeline.htr.applevision:build",
    "openrouter": "recipe_pipeline.htr.openrouter:build",  # cloud escalation for hard cursive
}


def known_backends() -> list[str]:
    return sorted(_REGISTRY)


def get_backend(name: str) -> HTRBackend:
    """Instantiate a backend by name. Raises ``KeyError`` for unknown names."""
    import importlib

    if name not in _REGISTRY:
        raise KeyError(f"Unknown OCR backend {name!r}. Known: {known_backends()}")
    module_path, factory = _REGISTRY[name].split(":")
    mod = importlib.import_module(module_path)
    return getattr(mod, factory)()
