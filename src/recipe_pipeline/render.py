"""Render a :class:`Recipe` to human-readable Markdown for review / manual upload to Mealie."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from recipe_pipeline.schema import Field_, Recipe


def _timing(label: str, field: Field_) -> str | None:
    if not field.value:
        return None
    tag = " _(estimated)_" if field.provenance == "estimated" else ""
    return f"**{label}:** {field.value}{tag}"


def recipe_to_markdown(recipe: Recipe) -> str:
    """Readable recipe with provenance cues — estimates and the import note are clearly marked."""
    lines: list[str] = [f"# {recipe.name}", ""]

    if recipe.description:
        lines += [recipe.description, ""]

    if recipe.import_note:
        # Blockquote at the top so a reader sees the caveat before cooking.
        lines.append("> ⚠️ **Import Software Note**")
        lines += [f"> {ln}" for ln in recipe.import_note.splitlines()]
        lines.append("")

    meta = [
        _timing("Servings", recipe.servings),
        _timing("Prep", recipe.prep_time),
        _timing("Cook", recipe.cook_time),
        _timing("Total", recipe.total_time),
    ]
    meta = [m for m in meta if m]
    if meta:
        lines += [" · ".join(meta), ""]

    lines += ["## Ingredients", ""]
    lines += [f"- {ing.text}" for ing in recipe.ingredients] or ["_(none detected)_"]
    lines += ["", "## Instructions", ""]
    steps = [f"{i}. {s.text}" for i, s in enumerate(recipe.instructions, 1)]
    lines += steps or ["_(none detected)_"]

    if recipe.tags or recipe.categories:
        lines += ["", f"**Tags:** {', '.join(recipe.categories + recipe.tags)}"]
    if recipe.dish_history:
        lines += ["", "## About this dish", "", recipe.dish_history]
    if recipe.source_curiosities:
        lines += ["", "## From the clipping (off-recipe)", "", recipe.source_curiosities]
    if recipe.original_transcription:
        lines += [
            "", "---", "", "<details><summary>Verbatim transcription</summary>", "",
            "```", recipe.original_transcription, "```", "</details>",
        ]
    return "\n".join(lines) + "\n"
