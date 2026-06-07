"""Quality gate: decide whether an extracted recipe is trustworthy or must be held for review.

Layered signals, cheapest first:
  1. image faintness  (near-blank/too-faint scan)        -> fail
  2. empty OCR        (engine found ~nothing)            -> fail
  3. [UNCLEAR] spans  (unresolved bits flagged by structure) -> warn (many -> fail)
  4. structure        (missing title/ingredients/steps)  -> fail/warn
  5. LLM review        (vision model rates the result)    -> warn/fail by confidence

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
EMPTY_OCR_CHARS = 12  # OCR shorter than this => nothing found
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


def assess_ocr(text: str) -> list[ReviewFlag]:
    """Flag a page where OCR found essentially nothing (blank/illegible scan)."""
    if len(text.strip()) < EMPTY_OCR_CHARS:
        return [ReviewFlag(code="empty_ocr", severity="fail", detail="OCR found ~nothing")]
    return []


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
        model=settings.vision_model,
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


# Human-friendly phrasing per flag code (falls back to the raw detail).
_FRIENDLY = {
    "unclear_spans": "Some text couldn't be read with full confidence (marked [UNCLEAR]).",
    "ocr_divergence": "The text-recognition engines disagreed on parts of this scan.",
    "missing_title": "No clear title was detected.",
    "missing_steps": "No preparation steps were detected.",
    "missing_ingredients": "No ingredients were detected.",
    "faint_scan": "The scan was faint or low-contrast.",
    "empty_ocr": "Very little text could be read from the scan.",
    "llm_dubious": "The automated review was not fully confident in this extraction.",
    "llm_issue": None,  # use the model's own concrete issue text
}


def format_import_note(report: ReviewReport) -> str | None:
    """Render a recipe's flags into a user-facing 'Import Software Note', or None if clean.

    Goes at the end of the recipe (Mealie note) so anyone cooking from it can see where the
    automated import was unsure and check the attached original scan.
    """
    if not report.flags:
        return None
    seen: list[str] = []
    for flag in report.flags:
        msg = _FRIENDLY.get(flag.code, flag.detail)
        if msg is None:  # llm_issue: surface the concrete issue
            msg = flag.detail
        if msg not in seen:
            seen.append(msg)
    bullets = "\n".join(f"- {m}" for m in seen)
    return (
        "This recipe was digitized automatically from the attached scan. The import software "
        "flagged a few things to double-check against the original:\n"
        f"{bullets}"
    )
