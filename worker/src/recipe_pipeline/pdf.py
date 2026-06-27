"""Render scanned PDF pages to images.

Uses ``pypdfium2`` (bundled PDFium binary) so there is no system dependency on Poppler — a
deliberate "future-me" choice for a tool that runs on a laptop.
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

# 300 DPI-equivalent render scale. PDFium's base is 72 DPI, so scale = target_dpi / 72.
DEFAULT_SCALE = 300 / 72


def render_pages(pdf_path: Path, scale: float = DEFAULT_SCALE) -> list[Image.Image]:
    """Render every page of ``pdf_path`` to a PIL image (RGB), in page order."""
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        images: list[Image.Image] = []
        for page in pdf:
            bitmap = page.render(scale=scale)
            images.append(bitmap.to_pil().convert("RGB"))
        return images
    finally:
        pdf.close()


def render_pages_to_dir(pdf_path: Path, out_dir: Path, scale: float = DEFAULT_SCALE) -> list[Path]:
    """Render pages to ``out_dir`` as ``page-001.jpg`` ... and return the written paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, img in enumerate(render_pages(pdf_path, scale=scale), start=1):
        p = out_dir / f"page-{i:03d}.jpg"
        img.save(p, "JPEG", quality=92)
        paths.append(p)
    return paths
