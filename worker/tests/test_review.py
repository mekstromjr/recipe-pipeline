"""Tests for the review/QA gate logic (the pure, non-model parts)."""

from __future__ import annotations

from recipe_pipeline import review
from recipe_pipeline_shared.schema import Ingredient, Recipe, ReviewFlag, ReviewStatus, Step


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


# --- glm-ocr failure modes ------------------------------------------------------------------
# Fixtures are REAL glm-ocr output captured from the samples corpus, not invented strings.

_FAINT_DEGENERATE = (
    "A blister filling\nconsistent always, shopped color,\n"
    "green more than possible texture\n" + "-\n" * 180
)
_GOOD = (
    "Jello - Cabbage Salad\n\n1 6 oz. pkg lime jello\n2 cups boiling water\n"
    "20 large marshmellows (cut up (or 4 cups small ones)\n2 cup crushed pineapple\n"
    "Mix jello and hot water until dissolved. Then add mellows and stir in hot mixture."
)


def test_degenerate_detects_repeated_single_character():
    """The faint page produced 400+ chars of '-' and passed the empty-OCR gate."""
    assert review.is_degenerate(_FAINT_DEGENERATE)


def test_degenerate_does_not_flag_real_transcript():
    assert not review.is_degenerate(_GOOD)


def test_degenerate_ignores_short_text():
    """Below the length floor the empty-OCR gate applies instead; don't double-judge."""
    assert not review.is_degenerate("1 cup flour")


def test_assess_ocr_fails_degenerate_output():
    flags = review.assess_ocr(_FAINT_DEGENERATE)
    assert [f.code for f in flags] == ["degenerate_ocr"]
    assert flags[0].severity == "fail"


def test_dedupe_collapses_exact_whole_transcript_repeat():
    text, partial = review.dedupe_repeats(_GOOD + _GOOD)
    assert text == _GOOD
    assert partial is False


def test_dedupe_leaves_clean_text_alone():
    text, partial = review.dedupe_repeats(_GOOD)
    assert text == _GOOD
    assert partial is False


def test_dedupe_flags_partial_repeat_without_editing():
    """A partial repeat may differ between copies, so choosing one would be a silent guess."""
    source = _GOOD + _GOOD[:90]
    text, partial = review.dedupe_repeats(source)
    assert partial is True
    assert text == source
