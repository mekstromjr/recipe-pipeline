"""Tests for the real (non-stub) ingest helpers."""

from __future__ import annotations

from pathlib import Path

from recipe_pipeline.ingest import RecipeJob, discover, slugify


def test_slugify_basic():
    assert slugify("Grandma's Apple Pie!") == "grandmas-apple-pie"
    assert slugify("  Crème  Brûlée  ") == "creme-brulee"
    assert slugify("") == "recipe"


def test_discover_finds_pdfs_one_per_recipe(tmp_inbox: Path):
    (tmp_inbox / "apple-pie.pdf").write_bytes(b"%PDF-1.4 fake")
    (tmp_inbox / "Beef Stew.PDF").write_bytes(b"%PDF-1.4 fake")
    (tmp_inbox / "notes.txt").write_text("ignore me")

    jobs = discover(tmp_inbox)

    assert all(isinstance(j, RecipeJob) for j in jobs)
    assert [j.slug for j in jobs] == ["apple-pie", "beef-stew"]


def test_discover_missing_inbox_returns_empty(tmp_path: Path):
    assert discover(tmp_path / "does-not-exist") == []
