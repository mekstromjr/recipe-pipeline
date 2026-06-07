"""Tests for the review/QA gate logic (the pure, non-model parts)."""

from __future__ import annotations

from recipe_pipeline import review
from recipe_pipeline.schema import Ingredient, Recipe, ReviewFlag, ReviewStatus, Step


def _recipe(**kw) -> Recipe:
    base = dict(
        name="Test",
        ingredients=[Ingredient(text="1 cup flour")],
        instructions=[Step(text="Mix.")],
    )
    base.update(kw)
    return Recipe(**base)


def test_build_report_status_levels():
    assert review.build_report([]).status == ReviewStatus.OK
    warn = [ReviewFlag(code="x", severity="warn", detail="d")]
    assert review.build_report(warn).status == ReviewStatus.NEEDS_REVIEW
    fail = [ReviewFlag(code="y", severity="fail", detail="d")]
    assert review.build_report(fail).status == ReviewStatus.FAILED
    # a single fail dominates a pile of warns
    assert review.build_report(warn + fail).status == ReviewStatus.FAILED


def test_only_ok_is_uploadable():
    assert review.build_report([]).uploadable() is True
    warned = review.build_report([ReviewFlag(code="x", severity="warn", detail="d")])
    assert warned.uploadable() is False


def test_assess_structure_flags_missing_parts():
    codes = {f.code for f in review.assess_structure(_recipe(ingredients=[], instructions=[]))}
    assert "missing_ingredients" in codes
    assert "missing_steps" in codes
    assert review.assess_structure(_recipe()) == []


def test_assess_ocr_empty_and_divergence():
    empty = review.assess_ocr("", "  ")
    assert any(f.code == "empty_ocr" for f in empty)
    # very different strings of adequate length -> divergence warning
    div = review.assess_ocr("the quick brown fox jumps", "zzz qqq vvv www mmm ккк")
    assert any(f.code == "ocr_divergence" for f in div)
    # near-identical -> no flags
    assert review.assess_ocr("2 cups flour and sugar", "2 cups flour and sugar") == []


def test_assess_unclear_thresholds():
    assert review.assess_unclear("all good here") is None
    one = review.assess_unclear("add [UNCLEAR: 2?] cups")
    assert one is not None and one.severity == "warn"
    many = review.assess_unclear("[UNCLEAR]" * review.MANY_UNCLEAR)
    assert many.severity == "fail"
