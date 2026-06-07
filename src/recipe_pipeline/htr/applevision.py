"""Apple Vision backend — macOS on-device OCR (VNRecognizeTextRequest).

Zero cost, fully on-device, no model download; Vision does its own layout/line segmentation and
returns per-line confidence. Language correction is disabled so it transcribes what's on the
card rather than "fixing" spelling (preserving e.g. "marshmellows") — normalization, if any,
happens later in the reconcile/structure stages.

Requires the `apple` extra (pyobjc Vision + Quartz). macOS only.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from recipe_pipeline.htr.base import HTRBackend, OcrLine, PageResult

if TYPE_CHECKING:
    from PIL import Image


def _import_frameworks():
    try:
        import Quartz
        import Vision
        from Foundation import NSURL
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise NotImplementedError(
            "Apple Vision backend needs the `apple` extra: `uv sync --extra apple` "
            "(pyobjc-framework-Vision + pyobjc-framework-Quartz). macOS only."
        ) from exc
    return Quartz, Vision, NSURL


class AppleVisionBackend:
    name = "applevision"

    def __init__(self, *, language_correction: bool = False) -> None:
        self._language_correction = language_correction
        self._Quartz, self._Vision, self._NSURL = _import_frameworks()

    def _recognize_file(self, path: Path) -> list[OcrLine]:
        Quartz, Vision, NSURL = self._Quartz, self._Vision, self._NSURL

        url = NSURL.fileURLWithPath_(str(path))
        source = Quartz.CGImageSourceCreateWithURL(url, None)
        if source is None:
            raise RuntimeError(f"Could not read image: {path}")
        cg_image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(self._language_correction)
        request.setRecognitionLanguages_(["en-US"])

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
        ok, err = handler.performRequests_error_([request], None)
        if not ok:
            raise RuntimeError(f"Vision request failed: {err}")

        observations = request.results() or []
        # Vision returns observations in no guaranteed order; sort top-to-bottom. boundingBox is
        # normalized with origin at the BOTTOM-left, so a larger y is higher on the page.
        observations = sorted(observations, key=lambda o: -o.boundingBox().origin.y)

        lines: list[OcrLine] = []
        for obs in observations:
            candidates = obs.topCandidates_(1)
            if not candidates:
                continue
            cand = candidates[0]
            box = obs.boundingBox()
            x0 = box.origin.x
            y0 = box.origin.y
            lines.append(
                OcrLine(
                    text=cand.string(),
                    confidence=float(cand.confidence()),
                    bbox=(x0, y0, x0 + box.size.width, y0 + box.size.height),
                )
            )
        return lines

    def transcribe_page(self, image: Image.Image) -> PageResult:
        # Vision reads from a CGImage; round-trip through a temp PNG (lossless, simple, robust).
        with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
            image.convert("RGB").save(tmp.name, "PNG")
            lines = self._recognize_file(Path(tmp.name))
        return PageResult.from_lines(lines)


def build() -> HTRBackend:
    return AppleVisionBackend()
