"""Ingredient parsing on Mealie import, and the re-parse backfill.

No database and no live Mealie: the client's HTTP is stubbed with an httpx MockTransport, so
these run in CI where the DB integration suite skips.
"""

from __future__ import annotations

import asyncio

import httpx
from recipe_pipeline_server.config import Settings
from recipe_pipeline_server.mealie import MealieClient, _plain_ingredient
from recipe_pipeline_server.reparse import _needs_parse, _original_lines
from recipe_pipeline_shared.mealie_payload import recipe_to_payload
from recipe_pipeline_shared.schema import Ingredient, Recipe


def _settings(**kw):
    return Settings(_env_file=None, mealie_api_token="t", **kw)


def _parser_reply(items: list[tuple[str, float, str]]) -> list[dict]:
    """(input, confidence, food name) -> the shape Mealie's parser returns."""
    return [
        {
            "input": text,
            "confidence": {"average": conf},
            "ingredient": {
                "quantity": 2.0,
                "unit": {"id": "u1", "name": "cup"},
                "food": {"id": None, "name": food},
                "note": "",
            },
        }
        for text, conf, food in items
    ]


def _client(handler) -> MealieClient:
    return MealieClient(_settings(), transport=httpx.MockTransport(handler))


def _parse(handler, texts: list[str]) -> list[dict]:
    """Drive the async client from a sync test.

    The repo has no async-test convention and pytest-asyncio is not a dependency; adding one
    for three tests is not worth it, and asyncio.run is exactly as faithful here.
    """

    async def go():
        client = _client(handler)
        try:
            return await client.parse_ingredients(texts)
        finally:
            await client.close()

    return asyncio.run(go())


def test_high_confidence_parse_is_structured_and_keeps_the_original_text():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/parser/ingredients":
            reply = _parser_reply([("2 cups white sugar", 0.99, "white sugar")])
            return httpx.Response(200, json=reply)
        if request.url.path == "/api/foods" and request.method == "GET":
            return httpx.Response(200, json={"items": []})
        if request.url.path == "/api/foods" and request.method == "POST":
            return httpx.Response(200, json={"id": "f1", "name": "white sugar"})
        raise AssertionError(request.url.path)

    out = _parse(handler, ["2 cups white sugar"])
    assert out[0]["food"] == {"id": "f1", "name": "white sugar"}
    assert out[0]["quantity"] == 2.0
    # The card's wording must survive the parse: the parser rewrites text (measured:
    # "marshmellows" -> "marshmallow"), and this pipeline preserves what the card said.
    assert out[0]["originalText"] == "2 cups white sugar"
    assert out[0]["disableAmount"] is False


def test_low_confidence_falls_back_to_a_plain_note():
    """Michael's threshold is 0.90. A weak parse is WORSE than none: unparsed text is merely
    unstructured, while a bad parse is wrong (measured: '1 8 oz pkg. Pecan nuts' -> food
    'pkg.', losing the actual food)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/parser/ingredients":
            reply = _parser_reply([("1 8 oz pkg. Pecan nuts", 0.77, "pkg.")])
            return httpx.Response(200, json=reply)
        raise AssertionError("must not resolve a food for a discarded parse")

    out = _parse(handler, ["1 8 oz pkg. Pecan nuts"])
    assert out == [_plain_ingredient("1 8 oz pkg. Pecan nuts")]
    assert out[0]["note"] == "1 8 oz pkg. Pecan nuts"


def test_parser_outage_degrades_to_plain_notes_rather_than_failing_the_upload():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    out = _parse(handler, ["2 cups white sugar"])
    assert out == [_plain_ingredient("2 cups white sugar")]


def test_payload_disables_amounts_only_when_nothing_was_parsed():
    r = Recipe(name="x", ingredients=[Ingredient(text="2 cups white sugar")])
    unparsed = recipe_to_payload(r, tags=[], categories=[])
    assert unparsed["settings"]["disableAmount"] is True
    parsed = recipe_to_payload(r, tags=[], categories=[], ingredients=[{"quantity": 2.0}])
    assert parsed["settings"]["disableAmount"] is False
    assert parsed["recipeIngredient"] == [{"quantity": 2.0}]


def test_reparse_detects_the_pre_parsing_shape():
    old = {"recipeIngredient": [{"note": "2 cups sugar", "originalText": None}]}
    new = {"recipeIngredient": [{"note": "", "originalText": "2 cups sugar"}]}
    assert _needs_parse(old) is True
    # originalText is the marker, so an already-parsed recipe is never rewritten twice.
    assert _needs_parse(new) is False
    assert _needs_parse({"recipeIngredient": []}) is False


def test_reparse_reads_lines_from_the_note_and_skips_blanks():
    doc = {"recipeIngredient": [{"note": "2 cups sugar"}, {"note": "  "}, {"note": "1 egg"}]}
    assert _original_lines(doc) == ["2 cups sugar", "1 egg"]
