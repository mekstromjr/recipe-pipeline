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


def test_uploadable_holds_only_failures():
    assert review.build_report([]).uploadable() is True  # OK
    # Minor issues now UPLOAD (with an Import Software Note), not held.
    warned = review.build_report([ReviewFlag(code="x", severity="warn", detail="d")])
    assert warned.uploadable() is True
    # Significant failures are still held.
    failed = review.build_report([ReviewFlag(code="y", severity="fail", detail="d")])
    assert failed.uploadable() is False


def test_format_import_note():
    assert review.format_import_note(review.build_report([])) is None
    rep = review.build_report(
        [ReviewFlag(code="unclear_spans", severity="warn", detail="1 unresolved")]
    )
    note = review.format_import_note(rep)
    assert note is not None
    assert "double-check" in note.lower()
    assert "couldn't be read" in note.lower()  # friendly phrasing, not the raw code


def test_assess_structure_flags_missing_parts():
    codes = {f.code for f in review.assess_structure(_recipe(ingredients=[], instructions=[]))}
    assert "missing_ingredients" in codes
    assert "missing_steps" in codes
    assert review.assess_structure(_recipe()) == []


def test_assess_ocr_empty():
    # near-empty OCR -> empty_ocr fail flag
    assert any(f.code == "empty_ocr" for f in review.assess_ocr("  "))
    # adequate text -> no flags
    assert review.assess_ocr("2 cups flour and sugar, mix well") == []


def test_assess_unclear_thresholds():
    assert review.assess_unclear("all good here") is None
    one = review.assess_unclear("add [UNCLEAR: 2?] cups")
    assert one is not None and one.severity == "warn"
    many = review.assess_unclear("[UNCLEAR]" * review.MANY_UNCLEAR)
    assert many.severity == "fail"
