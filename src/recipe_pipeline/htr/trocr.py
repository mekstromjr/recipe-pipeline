"""TrOCR backend (Microsoft ``trocr-large-handwritten``) — transformer architecture.

Best zero-shot CER on modern handwriting among local engines. TrOCR recognizes ONE text line
at a time, so this backend segments the page first (projection-profile, see ``segment.py``) and
runs the model per line on MPS where available. Confidence is sequence-level only, so it's
recorded as a coarse hint, not a gate.

Requires the `trocr` extra (`uv sync --extra trocr`): torch + transformers. The model (~1.3 GB)
downloads from Hugging Face on first use.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from recipe_pipeline import segment
from recipe_pipeline.htr.base import HTRBackend, OcrLine, PageResult

if TYPE_CHECKING:
    from PIL import Image

MODEL_ID = "microsoft/trocr-large-handwritten"


def _import_stack():
    try:
        import torch
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise NotImplementedError(
            "TrOCR backend needs the `trocr` extra: `uv sync --extra trocr` (torch + transformers)."
        ) from exc
    return torch, TrOCRProcessor, VisionEncoderDecoderModel


def _pick_device(torch):
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


@lru_cache(maxsize=1)
def _load():
    """Load processor + model once (cached). Heavy: ~1.3 GB download on first call."""
    torch, TrOCRProcessor, VisionEncoderDecoderModel = _import_stack()
    device = _pick_device(torch)
    processor = TrOCRProcessor.from_pretrained(MODEL_ID)
    model = VisionEncoderDecoderModel.from_pretrained(MODEL_ID).to(device)
    model.eval()
    return torch, processor, model, device


class TrOCRBackend:
    name = "trocr"

    def __init__(self) -> None:
        # Fail fast if the extra isn't installed; defer the heavy model load to first use.
        _import_stack()

    def transcribe_page(self, image: Image.Image) -> PageResult:
        torch, processor, model, device = _load()
        lines: list[OcrLine] = []
        for crop in segment.segment_lines(image):
            pixel_values = processor(
                images=crop.convert("RGB"), return_tensors="pt"
            ).pixel_values.to(device)
            with torch.no_grad():
                generated_ids = model.generate(pixel_values, max_new_tokens=128)
            text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()
            # Skip crops that decode to pure punctuation/symbols — TrOCR hallucinates these on
            # specks, fold lines, and near-blank bands.
            if text and any(ch.isalnum() for ch in text):
                lines.append(OcrLine(text=text))
        return PageResult.from_lines(lines)


def build() -> HTRBackend:
    return TrOCRBackend()
