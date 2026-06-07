"""Quality gate: decide whether an extracted recipe is trustworthy or must be held for review.

Layered signals, cheapest first:
  1. image faintness  (near-blank/too-faint scan)        -> fail
  2. empty OCR        (engines found ~nothing)           -> fail
  3. OCR divergence   (the two transcripts disagree a lot)-> warn
  4. [UNCLEAR] spans  (unresolved bits from reconcile)   -> warn (many -> fail)
  5. structure        (missing title/ingredients/steps)  -> fail/warn
  6. LLM review        (vision model rates the result)    -> warn/fail by confidence

Flagged recipes (status != ok) are HELD from upload. Reasons travel with the recipe.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from recipe_pipeline import ollama_client
from recipe_pipeline.prompts import REVIEW_SYSTEM_PROMPT
from recipe_pipeline.schema import ReviewFlag, ReviewReport, ReviewStatus

if TYPE_CHECKING:
    from PIL import Image

    from recipe_pipeline.config import Settings
    from recipe_pipeline.schema import Recipe

# Tunable thresholds (validated empirically against real scans).
FAINT_DARK_FRACTION = 0.004  # <0.4% truly-dark pixels => near-blank/too faint
FAINT_STD = 18.0  # low global contrast => washed out
EMPTY_OCR_CHARS = 12  # both transcripts shorter than this => nothing found
DIVERGENCE_CER = 0.6  # A-vs-B char error above this => engines strongly disagree
MANY_UNCLEAR = 6  # this many [UNCLEAR] spans => hard fail
LLM_FAIL_CONFIDENCE = 0.4
LLM_WARN_CONFIDENCE = 0.7


def assess_image(image: Image.Image) -> ReviewFlag | None:
    """Flag a page that is too faint / near-blank to trust."""
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    frac = float((gray < 128).mean())
    std = float(gray.std())
    if frac < FAINT_DARK_FRACTION or std < FAINT_STD:
        return ReviewFlag(
            code="faint_scan",
            severity="fail",
            detail=f"low ink/contrast (dark_fraction={frac:.4f}, std={std:.1f})",
        )
    return None


def assess_ocr(text_a: str, text_b: str) -> list[ReviewFlag]:
    """Flag empty OCR and strong A-vs-B divergence."""
    import jiwer

    flags: list[ReviewFlag] = []
    a, b = text_a.strip(), text_b.strip()
    if len(a) < EMPTY_OCR_CHARS and len(b) < EMPTY_OCR_CHARS:
        flags.append(
            ReviewFlag(code="empty_ocr", severity="fail", detail="both OCR engines found ~nothing")
        )
        return flags  # divergence is meaningless if both are empty
    if a and b:
        cer = jiwer.cer(a.lower(), b.lower())
        if cer > DIVERGENCE_CER:
            flags.append(
                ReviewFlag(
                    code="ocr_divergence",
                    severity="warn",
                    detail=f"OCR engines disagree strongly (A/B CER={cer:.2f})",
                )
            )
    return flags


def assess_unclear(reconciled_text: str) -> ReviewFlag | None:
    """Flag based on how many [UNCLEAR ...] markers the reconcile stage left."""
    n = reconciled_text.upper().count("[UNCLEAR")
    if n == 0:
        return None
    severity = "fail" if n >= MANY_UNCLEAR else "warn"
    return ReviewFlag(
        code="unclear_spans", severity=severity, detail=f"{n} unresolved [UNCLEAR] span(s)"
    )


def assess_structure(recipe: Recipe) -> list[ReviewFlag]:
    """Flag a recipe missing its essential parts."""
    flags: list[ReviewFlag] = []
    if not (recipe.name and recipe.name.strip()):
        flags.append(ReviewFlag(code="missing_title", severity="warn", detail="no title"))
    if not recipe.ingredients:
        flags.append(
            ReviewFlag(code="missing_ingredients", severity="fail", detail="no ingredients")
        )
    if not recipe.instructions:
        flags.append(ReviewFlag(code="missing_steps", severity="warn", detail="no instructions"))
    return flags


def _parse_json(text: str) -> dict:
    """Extract the first JSON object from a model reply (tolerating code fences/prose)."""
    s = text.strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in reply: {text[:120]!r}")
    return json.loads(s[start : end + 1])


def llm_review(
    settings: Settings, recipe: Recipe, page_images: list[Path]
) -> tuple[float, list[ReviewFlag]]:
    """Vision-LLM review: compare the recipe to the image, return (confidence, flags)."""
    recipe_text = recipe.model_dump_json(indent=2)
    reply = ollama_client.chat(
        settings,
        system=REVIEW_SYSTEM_PROMPT,
        user=f"Structured recipe:\n{recipe_text}\n\nReview it against the page image(s).",
        images=page_images,
        model=settings.reconcile_model,  # the vision model
    )
    data = _parse_json(reply)
    confidence = float(data.get("confidence", 0.0))
    issues = data.get("issues") or []
    flags: list[ReviewFlag] = []
    if confidence < LLM_FAIL_CONFIDENCE:
        flags.append(
            ReviewFlag(
                code="llm_dubious", severity="fail", detail=f"low confidence {confidence:.2f}"
            )
        )
    elif confidence < LLM_WARN_CONFIDENCE or not data.get("looks_complete", True):
        flags.append(
            ReviewFlag(
                code="llm_dubious", severity="warn", detail=f"confidence {confidence:.2f}"
            )
        )
    for issue in issues[:5]:
        flags.append(ReviewFlag(code="llm_issue", severity="warn", detail=str(issue)))
    return confidence, flags


def build_report(flags: list[ReviewFlag], *, confidence: float | None = None) -> ReviewReport:
    """Combine flags into a status: any 'fail' -> FAILED; any 'warn' -> NEEDS_REVIEW; else OK."""
    if any(f.severity == "fail" for f in flags):
        status = ReviewStatus.FAILED
    elif flags:
        status = ReviewStatus.NEEDS_REVIEW
    else:
        status = ReviewStatus.OK
    return ReviewReport(status=status, confidence=confidence, flags=flags)
