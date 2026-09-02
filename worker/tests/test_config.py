"""Guard the PROJECT_ROOT anchor: data dirs resolve to the REPO root, not worker/.

Regression test for the #2-restructure bug where moving config.py from src/ to worker/src/
silently shifted every relative dir (inbox/output/samples) into worker/, emptying the review
queue and scattering batch artifacts.
"""

from recipe_pipeline.config import PROJECT_ROOT, Settings


def test_project_root_is_repo_root():
    # The repo root is the uv workspace root: it contains the three member packages.
    assert (PROJECT_ROOT / "worker" / "src" / "recipe_pipeline").is_dir()
    assert (PROJECT_ROOT / "shared").is_dir()
    assert (PROJECT_ROOT / "server").is_dir()


def test_relative_dirs_anchor_to_repo_root(monkeypatch):
    monkeypatch.delenv("RECIPE_OUTPUT_DIR", raising=False)
    s = Settings(_env_file=None)
    assert s.outbox == PROJECT_ROOT / "output"
    assert s.inbox == PROJECT_ROOT / "inbox"


def test_ollama_schema_marks_content_fields_required():
    """Ollama's `format` is constrained decoding: with no `required`, `{}` validates and
    qwen3.5:4b emits it. Measured — a perfect transcript yielded an almost-empty object."""
    from recipe_pipeline.structure import REQUIRED_FIELDS, _ollama_schema

    schema = _ollama_schema()
    assert schema["required"] == REQUIRED_FIELDS
    for field in ("name", "ingredients", "instructions"):
        assert field in schema["required"]


def test_ollama_schema_does_not_mutate_the_model_schema():
    """The pydantic model must stay lenient for PARSING; only the sent schema is strict."""
    from recipe_pipeline.structure import StructuredCard, _ollama_schema

    _ollama_schema()
    assert "required" not in StructuredCard.model_json_schema()
