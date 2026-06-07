"""Line segmentation: a page image -> ordered text-line image crops.

Uses a classic **projection-profile** method (binarize, sum ink per row, split on the gaps
between text lines) — dependency-light (numpy + scikit-image's Otsu, already present via
``deskew``) and well-suited to the mostly single-column recipe cards. Engines that segment
internally (Apple Vision, Kraken) don't use this; TrOCR does.
"""

from __future__ import annotations

import numpy as np
from PIL import Image
from skimage.filters import threshold_otsu


def _ink_rows(gray: np.ndarray) -> np.ndarray:
    """Boolean per-row mask: True where the row contains a meaningful amount of dark ink."""
    try:
        thresh = threshold_otsu(gray)
    except ValueError:  # flat image
        return np.zeros(gray.shape[0], dtype=bool)
    ink = gray < thresh  # dark pixels = ink
    row_counts = ink.sum(axis=1)
    # A row "has text" if its ink count exceeds a small fraction of the page width.
    min_ink = max(3, int(0.005 * gray.shape[1]))
    return row_counts > min_ink


def _bands(mask: np.ndarray, *, min_gap: int, min_height: int) -> list[tuple[int, int]]:
    """Group consecutive True rows into (start, end) bands, merging gaps smaller than min_gap."""
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for i, on in enumerate(mask):
        if on and start is None:
            start = i
        elif not on and start is not None:
            bands.append((start, i))
            start = None
    if start is not None:
        bands.append((start, len(mask)))

    # Merge bands separated by a tiny gap (descenders/ascenders, dotted i's).
    merged: list[tuple[int, int]] = []
    for b in bands:
        if merged and b[0] - merged[-1][1] < min_gap:
            merged[-1] = (merged[-1][0], b[1])
        else:
            merged.append(b)
    return [(s, e) for s, e in merged if e - s >= min_height]


def segment_lines(image: Image.Image, *, pad: int = 8) -> list[Image.Image]:
    """Return ordered line-image crops for ``image`` (reading order: top-to-bottom).

    Each crop is trimmed to the line's actual ink extent (with a little padding) — recognizers
    like TrOCR expect tight single-line images and hallucinate in large blank margins.
    """
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    h, w = gray.shape
    try:
        thresh = threshold_otsu(gray)
    except ValueError:
        return []
    ink = gray < thresh
    mask = _ink_rows(gray)
    if not mask.any():
        return []

    # Scale thresholds to image size so it works across render DPIs.
    bands = _bands(mask, min_gap=max(4, h // 200), min_height=max(8, h // 150))

    crops: list[Image.Image] = []
    for y0, y1 in bands:
        top = max(0, y0 - pad)
        bottom = min(h, y1 + pad)
        # Horizontal extent of this line's ink, so we don't feed wide blank margins.
        cols = ink[y0:y1].any(axis=0)
        xs = np.flatnonzero(cols)
        if xs.size == 0:
            continue
        left = max(0, int(xs[0]) - pad)
        right = min(w, int(xs[-1]) + 1 + pad)
        crops.append(image.crop((left, top, right, bottom)))
    return crops
