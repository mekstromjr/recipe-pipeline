"""Unit tests for the review editor's field semantics (#17).

Deliberately NOT in test_review_api.py: that module is a DB integration suite and skips
entirely without RECIPE_TEST_DB_URL, so these would have silently never run. Nothing here
needs a database.
"""

from __future__ import annotations

# --- editable yield/time/curiosities fields (#17) ---------------------------------------------

def test_apply_timed_marks_an_edit_human_not_from_card():
    """A reviewer typing a yield is authoritative but is NOT the card saying it."""
    from recipe_pipeline_server.api.review import _apply_timed
    from recipe_pipeline_shared.schema import Field_, Provenance

    out = _apply_timed(Field_(value="2 dozen", provenance=Provenance.ESTIMATED), "3 dozen")
    assert out.value == "3 dozen"
    assert out.provenance is Provenance.HUMAN


def test_apply_timed_none_means_not_submitted():
    """None must leave the field untouched, provenance included — a partial form
    submission must not wipe fields it did not include."""
    from recipe_pipeline_server.api.review import _apply_timed
    from recipe_pipeline_shared.schema import Field_, Provenance

    original = Field_(value="3 dozen", provenance=Provenance.FROM_CARD)
    out = _apply_timed(original, None)
    assert out.value == "3 dozen"
    assert out.provenance is Provenance.FROM_CARD


def test_apply_timed_empty_string_clears_the_field():
    """"" is a real edit meaning 'clear this', distinct from None."""
    from recipe_pipeline_server.api.review import _apply_timed
    from recipe_pipeline_shared.schema import Field_, Provenance

    out = _apply_timed(Field_(value="3 dozen", provenance=Provenance.FROM_CARD), "")
    assert out.value is None
    # A cleared field has nothing to attribute. Provenance is meaningless with no value, and
    # estimated_fields() already ignores valueless fields, so it is left at the default.
    assert out.provenance is Provenance.FROM_CARD


def test_apply_timed_no_op_edit_keeps_original_provenance():
    """Re-saving an unchanged form must not relabel card content as a human edit."""
    from recipe_pipeline_server.api.review import _apply_timed
    from recipe_pipeline_shared.schema import Field_, Provenance

    out = _apply_timed(Field_(value="3 dozen", provenance=Provenance.FROM_CARD), "3 dozen")
    assert out.provenance is Provenance.FROM_CARD


def test_human_edits_are_not_reported_as_ai_estimated():
    """ai_estimated_fields drives the Mealie extra and the import note; a value a person
    typed must never be described to the reader as AI-estimated."""
    from recipe_pipeline_shared.schema import Field_, Provenance, Recipe

    r = Recipe(
        name="x",
        recipe_yield=Field_(value="3 dozen", provenance=Provenance.HUMAN),
        cook_time=Field_(value="20 min", provenance=Provenance.ESTIMATED),
    )
    assert r.estimated_fields() == ["cook_time"]
