"""Tests for enrichment's additive-only guarantee and JSON tolerance."""

from __future__ import annotations

from recipe_pipeline.enrich import _set_if_empty
from recipe_pipeline.structure import _json_only
from recipe_pipeline_shared.schema import Field_, Provenance


def test_estimate_fills_only_empty_fields():
    # Empty field gets the estimate, tagged ESTIMATED.
    filled = _set_if_empty(Field_(), "30 minutes")
    assert filled.value == "30 minutes"
    assert filled.provenance == Provenance.ESTIMATED


def test_estimate_never_overwrites_card_value():
    from_card = Field_(value="45 minutes", provenance=Provenance.FROM_CARD)
    result = _set_if_empty(from_card, "30 minutes")
    # The card's value (and provenance) must be preserved — estimates never clobber it.
    assert result.value == "45 minutes"
    assert result.provenance == Provenance.FROM_CARD


def test_json_only_strips_fences_and_prose():
    assert _json_only('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _json_only('Here you go: {"a": 1} thanks') == '{"a": 1}'
