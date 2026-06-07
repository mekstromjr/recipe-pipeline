"""Internal recipe data model.

The guiding rule of this whole project: **never blend AI-generated content into what was
actually written on the card.** Every field that can carry inferred data records its
:class:`Provenance`, and anything off-recipe (clipping backs, AI estimates, dish history) is
kept separate so it lands in Mealie's free-form ``extras`` rather than the canonical fields.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Provenance(StrEnum):
    """Where a piece of data came from."""

    FROM_CARD = "from_card"  # transcribed from the scan (authoritative)
    ENRICHED = "enriched"  # added from a web source (e.g. dish history, cuisine)
    ESTIMATED = "estimated"  # inferred/guessed by a model (e.g. cook time not on the card)


class Ingredient(BaseModel):
    text: str
    provenance: Provenance = Provenance.FROM_CARD


class Step(BaseModel):
    text: str
    provenance: Provenance = Provenance.FROM_CARD


class Field_(BaseModel):
    """A scalar value plus its provenance (for fields that may be card-sourced OR estimated)."""

    value: str | None = None
    provenance: Provenance = Provenance.FROM_CARD


class Recipe(BaseModel):
    """The pipeline's canonical recipe representation, later mapped to Mealie ``Recipe-Input``."""

    # --- Core (from the card) ---
    name: str
    description: str | None = None
    ingredients: list[Ingredient] = Field(default_factory=list)
    instructions: list[Step] = Field(default_factory=list)

    # --- Yield / timing (card value if present, else model estimate — provenance tracked) ---
    servings: Field_ = Field(default_factory=Field_)
    recipe_yield: Field_ = Field(default_factory=Field_)
    prep_time: Field_ = Field(default_factory=Field_)
    cook_time: Field_ = Field(default_factory=Field_)
    total_time: Field_ = Field(default_factory=Field_)

    # --- Enrichment (additive; never card content) ---
    categories: list[str] = Field(default_factory=list)  # meal / course
    tags: list[str] = Field(default_factory=list)  # cuisine / genre
    dish_history: str | None = None  # 1-paragraph, enriched

    # --- Provenance / quality bookkeeping (-> Mealie extras) ---
    source: str | None = None  # e.g. "Grandma's recipe box", "newspaper clipping"
    original_transcription: str | None = None  # verbatim reconciled text, all pages
    unclear_spans: list[str] = Field(default_factory=list)  # things needing human review
    source_curiosities: str | None = None  # quaint off-recipe content (e.g. back of a clipping)
    ocr_confidence: float | None = None

    # --- Source files ---
    source_pdf: str | None = None  # path to the original scanned PDF
    page_images: list[str] = Field(default_factory=list)  # rendered page image paths

    def estimated_fields(self) -> list[str]:
        """Names of timing/yield fields that were model-estimated rather than read from the card."""
        out: list[str] = []
        for fname in ("servings", "recipe_yield", "prep_time", "cook_time", "total_time"):
            f: Field_ = getattr(self, fname)
            if f.value is not None and f.provenance == Provenance.ESTIMATED:
                out.append(fname)
        return out


class ReviewStatus(StrEnum):
    """Outcome of the quality gate."""

    OK = "ok"  # clean — eligible for upload
    NEEDS_REVIEW = "needs_review"  # held: looks dubious, a human should check
    FAILED = "failed"  # held: hard failure (faint/blank scan, empty OCR) — likely unusable


class ReviewFlag(BaseModel):
    """One reason a recipe was held for review."""

    code: str  # e.g. faint_scan, empty_ocr, ocr_divergence, unclear_spans, missing_*, llm_dubious
    severity: str  # "fail" (hard) | "warn" (soft)
    detail: str


class ReviewReport(BaseModel):
    """Quality assessment for one extracted recipe. Flagged recipes are HELD from upload."""

    status: ReviewStatus
    confidence: float | None = None  # 0-1, from the LLM review pass
    flags: list[ReviewFlag] = Field(default_factory=list)

    def uploadable(self) -> bool:
        """Only clean recipes upload; needs_review and failed are both held back."""
        return self.status == ReviewStatus.OK
