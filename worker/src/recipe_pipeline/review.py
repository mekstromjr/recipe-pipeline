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
from PIL import Image
from recipe_pipeline_shared.schema import ReviewFlag, ReviewReport, ReviewStatus

from recipe_pipeline import ollama_client
from recipe_pipeline.htr import glmocr
from recipe_pipeline.prompts import REVIEW_SYSTEM_PROMPT

if TYPE_CHECKING:
    from recipe_pipeline_shared.schema import Recipe

    from recipe_pipeline.config import Settings

# Tunable thresholds (validated empirically against real scans).
FAINT_DARK_FRACTION = 0.004  # <0.4% truly-dark pixels => near-blank/too faint
FAINT_STD = 18.0  # low global contrast => washed out
EMPTY_OCR_CHARS = 12  # OCR shorter than this => nothing found
MANY_UNCLEAR = 6  # this many [UNCLEAR] spans => hard fail
LLM_FAIL_CONFIDENCE = 0.4
LLM_WARN_CONFIDENCE = 0.7

# --- glm-ocr-specific failure modes (MEASURED, not anticipated) -----------------------------
# Both were observed on this corpus and neither is caught by EMPTY_OCR_CHARS, because both
# produce plenty of characters. See docs/redesign-v2-plan.md.
#
# 1. Degenerate output: on faint/low-signal pages glm-ocr emits a long run of a single
#    character ("-\n-\n-\n..."). The faint sample produced 1434 such characters, which
#    sailed through the empty-OCR gate as a clean result.
# 2. Repetition: the model re-emits the whole transcript 2-3x verbatim. Seen in 4 of 5 test
#    pages. A clean whole-transcript repeat is recoverable (de-duplicate); a partial one is
#    not, so it is flagged rather than silently "fixed".
DEGENERATE_MIN_CHARS = 40  # below this, EMPTY_OCR_CHARS/short-page handling already applies
DEGENERATE_ALNUM_RATIO = 0.35  # real recipe text measures ~0.6-0.75 alphanumeric
DEGENERATE_DISTINCT_CHARS = 12  # a page of "-\n" has ~2; the faint sample's tail had ~6
REPEAT_MIN_BLOCK_CHARS = 60  # ignore trivially short "repeats" (a recurring line is not one)


def is_faint(image: Image.Image) -> bool:
    """True if a page is faint / near-blank (low ink fraction or low contrast)."""
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    frac = float((gray < 128).mean())
    std = float(gray.std())
    return frac < FAINT_DARK_FRACTION or std < FAINT_STD


def is_degenerate(text: str) -> bool:
    """True if OCR returned filler rather than content (a long run of one or two characters).

    Judged on character DIVERSITY, not length: the failure produces hundreds of valid
    characters, so any length-based check passes it.
    """
    stripped = "".join(text.split())
    if len(stripped) < DEGENERATE_MIN_CHARS:
        return False  # too short to judge; the empty-OCR gate covers this range
    alnum_ratio = sum(c.isalnum() for c in stripped) / len(stripped)
    return alnum_ratio < DEGENERATE_ALNUM_RATIO or len(set(stripped)) < DEGENERATE_DISTINCT_CHARS


def dedupe_repeats(text: str) -> tuple[str, bool]:
    """Collapse a whole-transcript repeat. Returns (text, was_partial_repeat).

    glm-ocr often emits the transcript 2-3x verbatim. When the tail is an exact repeat of the
    head, dropping it is lossless. When it repeats only PARTIALLY the two copies may differ,
    and picking one would be a silent guess — so the text is left intact and the caller is
    told to flag it for a human.
    """
    body = text.strip()
    if len(body) < REPEAT_MIN_BLOCK_CHARS * 2:
        return text, False

    # Exact whole-transcript repeat: the string is its own prefix repeated k times.
    for k in (2, 3, 4):
        if len(body) % k == 0:
            unit = body[: len(body) // k]
            if len(unit) >= REPEAT_MIN_BLOCK_CHARS and unit * k == body:
                return unit, False

    # Otherwise look for the longest prefix that recurs later in the text. A long recurrence
    # that does not tile the whole string is a PARTIAL repeat: real content may differ between
    # the copies, so report it instead of choosing one.
    head = body[:REPEAT_MIN_BLOCK_CHARS]
    if body.find(head, REPEAT_MIN_BLOCK_CHARS) != -1:
        return text, True
    return text, False


def assess_ocr(text: str) -> list[ReviewFlag]:
    """Flag a page where OCR found nothing, or returned filler instead of content."""
    if len(text.strip()) < EMPTY_OCR_CHARS:
        return [ReviewFlag(code="empty_ocr", severity="fail", detail="OCR found ~nothing")]
    if is_degenerate(text):
        return [
            ReviewFlag(
                code="degenerate_ocr",
                severity="fail",
                detail="OCR returned filler (repeated single character), not text",
            )
        ]
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
    # Downscale before sending: the vision encoder's cost scales with pixel count, and a
    # full-resolution scan buys no accuracy at this model size.
    images = [
        glmocr.downscale(Image.open(p).convert("RGB"), settings.ocr_max_edge)
        for p in page_images
    ]
    reply = ollama_client.chat(
        settings,
        system=REVIEW_SYSTEM_PROMPT,
        user=f"Structured recipe:\n{recipe_text}\n\nReview it against the page image(s).",
        images=images,
        model=settings.text_model,
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
