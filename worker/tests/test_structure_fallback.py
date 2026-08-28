"""Degenerate schema-constrained output falls back to a free-form retry.

Ollama 0.32.x + gemma4:e4b returns a bare `{}` under a JSON-schema `format` constraint —
valid JSON, so it validates into an all-defaults card and CANNOT be caught as a parse
error. structure() must detect the emptiness and retry unconstrained.
"""

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


def test_both_replies_bad_still_yields_reviewable_card(monkeypatch):
    monkeypatch.setattr(
        structure_mod.ollama_client, "chat",
        lambda settings, **kw: "utter garbage not json",
    )
    recipe = structure_mod.structure(
        _settings(), ocr_pages=["Pecan Pie\nstuff"], source_pdf="x.pdf"
    )
    assert recipe.name == "Pecan Pie"  # first OCR line fallback
    assert recipe.ingredients == []
