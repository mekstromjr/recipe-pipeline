"""Turn the OCR transcription into a structured :class:`~recipe_pipeline_shared.schema.Recipe`.

Also applies the relevance filter for newspaper clippings (see prompts.STRUCTURE_SYSTEM_PROMPT):
recipe content only; keep+[UNCLEAR] when unsure rather than silently dropping; quaint off-recipe
text -> ``source_curiosities``. All produced fields are ``Provenance.FROM_CARD`` here —
enrichment/estimates are added later, separately. The verbatim OCR text is preserved on
``original_transcription`` per the keep-original decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from recipe_pipeline_shared.schema import Field_, Ingredient, Provenance, Recipe, Step

from recipe_pipeline import ollama_client
from recipe_pipeline.prompts import STRUCTURE_SYSTEM_PROMPT

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings


class StructureError(RuntimeError):
    """The model returned something unusable — distinct from a card that genuinely has no
    ingredients. The pipeline must be able to tell those apart in review."""


# Fields the model MUST emit. Ollama's `format` is CONSTRAINED DECODING: it guarantees the
# output validates against the schema, nothing more. With no `required` list, `{}` validates,
# and qwen3.5:4b takes that cheapest legal path — measured: a perfect OCR transcript produced
# `{"recipe_yield": "", "source_curiosities": null}` and nothing else.
#
# These are marked required in the SCHEMA SENT TO OLLAMA only; the Pydantic model below stays
# lenient so parsing never crashes. `name` types as "string", so requiring it yields "" rather
# than the null that caused the original nightly retry loop.
REQUIRED_FIELDS = ["name", "ingredients", "instructions"]


def _ollama_schema() -> dict:
    """StructuredCard's JSON schema with the content fields marked required."""
    schema = StructuredCard.model_json_schema()
    schema["required"] = list(REQUIRED_FIELDS)
    return schema


class StructuredCard(BaseModel):
    """Flat shape the LLM fills (constrained via Ollama JSON-schema output)."""

    # Defaulted (was required): the model sometimes returns null `name` (e.g. a page with no
    # clear title), which crashed validation and put the recipe in a nightly retry loop.
    name: str = ""
    description: str | None = None
    ingredients: list[str] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)
    servings: str | None = None
    recipe_yield: str | None = None
    prep_time: str | None = None
    cook_time: str | None = None
    total_time: str | None = None
    source_curiosities: str | None = None


def _from_card(value: str | None) -> Field_:
    return Field_(value=value, provenance=Provenance.FROM_CARD) if value else Field_()


def _json_only(text: str) -> str:
    """Extract the JSON object from a reply, tolerating code fences / stray prose."""
    s = text.strip()
    start, end = s.find("{"), s.rfind("}")
    return s[start : end + 1] if start != -1 and end != -1 else s


def _degenerate(card: StructuredCard) -> bool:
    """An 'answer' with no name, ingredients, or steps is no answer at all."""
    return not card.name and not card.ingredients and not card.instructions

def empty_recipe(*, ocr_pages: list[str], source_pdf: str) -> Recipe:
    """A content-free Recipe that still carries the OCR text, for when structuring fails.

    The transcription is the expensive part and it succeeded, so it must survive: a human in
    the review queue can work from it even though no fields were extracted.
    """
    ocr_text = "\n\n".join(ocr_pages)
    first = next((ln.strip() for ln in ocr_text.splitlines() if ln.strip()), "")
    return Recipe(
        name=first[:60] or "Untitled (needs review)",
        original_transcription=ocr_text,
        source_pdf=source_pdf,
    )


def structure(
    settings: Settings,
    *,
    ocr_pages: list[str],
    source_pdf: str,
    verbatim: str | None = None,
) -> Recipe:
    """Build a :class:`Recipe` from the per-page OCR text (concatenated into one recipe)."""
    ocr_text = "\n\n".join(ocr_pages)

    def _ask(fmt, user_suffix: str = "") -> StructuredCard | None:
        reply = ollama_client.chat(
            settings,
            system=STRUCTURE_SYSTEM_PROMPT,
            user=f"OCR transcription (all pages):\n\n{ocr_text}{user_suffix}",
            model=settings.text_model,
            format=fmt,
        )
        try:
            return StructuredCard.model_validate_json(_json_only(reply))
        except Exception:
            # Malformed model output must not crash the batch (it would retry forever).
            return None

    # Attempt 1: schema-constrained, with the content fields REQUIRED. Constrained decoding
    # only guarantees the reply VALIDATES — with every field optional, `{}` validates, so the
    # collapse below is the model taking the cheapest legal path. Requiring the content fields
    # removes that path, which is cheaper than detecting the collapse and paying for a retry.
    card = _ask(_ollama_schema())
    if (card is None or _degenerate(card)) and ocr_text.strip():
        # Attempt 2 (kept as the safety net): some Ollama/model pairings collapse to `{}` even
        # so — observed on 0.32.14 + gemma4:e4b, where the UNCONSTRAINED reply is a perfect
        # answer. `{}` is valid JSON, so this cannot be caught as a parse error; it must be
        # caught as emptiness. Unconstrained, the model invents its own keys ("title", nested
        # objects...), so the retry prompt pins the EXACT flat shape it must produce.
        keys = (
            'Respond with ONLY a JSON object using exactly these keys: "name" (string), '
            '"description" (string or null), "ingredients" (array of strings), '
            '"instructions" (array of strings), "servings", "recipe_yield", "prep_time", '
            '"cook_time", "total_time", "source_curiosities" (each string or null). '
            "No markdown fence, no prose."
        )
        retry = _ask(None, user_suffix=f"\n\n{keys}")
        if retry is not None and not _degenerate(retry):
            card = retry
    if card is None or _degenerate(card):
        # BOTH attempts failed. That is a model failure, not a card that happens to be empty,
        # and the two must stay distinguishable: falling back to an empty card here makes
        # review show "no ingredients", which reads as a fact about the scan.
        raise StructureError(
            "structuring produced no name, ingredients or instructions "
            "(schema-constrained and free-form attempts both failed)"
        )

    # A page with no readable title still gets a usable name (the gate flags it for review).
    name = card.name.strip() if card.name else ""
    if not name:
        first = next((ln.strip() for ln in ocr_text.splitlines() if ln.strip()), "")
        name = (first[:60] or "Untitled (needs review)")

    return Recipe(
        name=name,
        description=card.description,
        ingredients=[Ingredient(text=t) for t in card.ingredients],
        instructions=[Step(text=t) for t in card.instructions],
        servings=_from_card(card.servings),
        recipe_yield=_from_card(card.recipe_yield),
        prep_time=_from_card(card.prep_time),
        cook_time=_from_card(card.cook_time),
        total_time=_from_card(card.total_time),
        source_curiosities=card.source_curiosities,
        original_transcription=verbatim if verbatim is not None else ocr_text,
        source_pdf=source_pdf,
    )
