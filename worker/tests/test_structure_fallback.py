"""Degenerate schema-constrained output falls back to a free-form retry.

Ollama 0.32.x + gemma4:e4b returns a bare `{}` under a JSON-schema `format` constraint —
valid JSON, so it validates into an all-defaults card and CANNOT be caught as a parse
error. structure() must detect the emptiness and retry unconstrained.
"""

import pytest
from recipe_pipeline import structure as structure_mod
from recipe_pipeline.config import Settings

GOOD = '{"name": "Fudge", "ingredients": ["2 cup sugar"], "instructions": ["Boil."]}'


def _settings():
    return Settings(_env_file=None)


def test_empty_schema_reply_retries_unconstrained(monkeypatch):
    calls = []

    def fake_chat(settings, *, system, user, model, format=None):
        calls.append(format)
        return "{}" if format is not None else GOOD

    monkeypatch.setattr(structure_mod.ollama_client, "chat", fake_chat)
    recipe = structure_mod.structure(
        _settings(), ocr_pages=["Fudge\n2 cup sugar\nBoil."], source_pdf="x.pdf"
    )
    assert [f is not None for f in calls] == [True, False]  # schema first, then free-form
    assert recipe.name == "Fudge"
    assert [i.text for i in recipe.ingredients] == ["2 cup sugar"]


def test_good_schema_reply_does_not_retry(monkeypatch):
    calls = []

    def fake_chat(settings, *, system, user, model, format=None):
        calls.append(format)
        return GOOD

    monkeypatch.setattr(structure_mod.ollama_client, "chat", fake_chat)
    recipe = structure_mod.structure(_settings(), ocr_pages=["x"], source_pdf="x.pdf")
    assert len(calls) == 1
    assert recipe.name == "Fudge"


def test_both_replies_bad_raises_rather_than_returning_an_empty_card(monkeypatch):
    """When BOTH attempts fail, that is a model failure — not a card with no ingredients.

    This test previously asserted that structure() returned a reviewable card here. The
    guarantee it protects (never lose the scan) is unchanged, but it now lives one layer up:
    extract_recipe catches StructureError, builds the reviewable card via empty_recipe(), and
    attaches a distinct `structure_failed` flag. Returning the empty card from here made
    review show "no ingredients", which reads as a fact about the SCAN rather than about the
    model — the silent-plausible-failure class this pipeline exists to avoid.
    """
    monkeypatch.setattr(
        structure_mod.ollama_client, "chat",
        lambda settings, **kw: "utter garbage not json",
    )
    with pytest.raises(structure_mod.StructureError):
        structure_mod.structure(_settings(), ocr_pages=["Pecan Pie\nstuff"], source_pdf="x.pdf")


def test_empty_recipe_preserves_the_scan_for_review():
    """The other half of the guarantee: the expensive part (OCR) must survive the failure."""
    recipe = structure_mod.empty_recipe(ocr_pages=["Pecan Pie\nstuff"], source_pdf="x.pdf")
    assert recipe.name == "Pecan Pie"  # first OCR line fallback
    assert recipe.ingredients == []
    assert "stuff" in recipe.original_transcription
