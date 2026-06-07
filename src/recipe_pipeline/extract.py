"""End-to-end extraction: one recipe PDF -> a reviewed :class:`Recipe` written to ``output/``.

Orchestration glue. Early stages (discover, render, OCR, reconcile, review signals) are real;
``structure``/``enrich`` are still stubbed, so running it today exercises the wiring and stops
at the first unimplemented stage. Quality flags are accumulated throughout and written to a
``review.json`` that gates upload (flagged recipes are HELD).

Flow:  ingest -> render -> preprocess -> OCR x2 -> reconcile -> structure -> enrich -> review.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PIL import Image

from recipe_pipeline import enrich as enrich_mod
from recipe_pipeline import ingest, pdf, preprocess, reconcile, review, structure
from recipe_pipeline.htr import get_backend
from recipe_pipeline.schema import ReviewFlag, ReviewStatus

if TYPE_CHECKING:
    from pathlib import Path

    from recipe_pipeline.config import Settings
    from recipe_pipeline.ingest import RecipeJob
    from recipe_pipeline.schema import Recipe, ReviewReport


def extract_recipe(settings: Settings, job: RecipeJob) -> tuple[Recipe, ReviewReport]:
    """Run the full pipeline for one recipe PDF; return the recipe and its review report."""
    out_dir = settings.outbox / job.slug
    pages_dir = out_dir / "pages"
    page_images = pdf.render_pages_to_dir(job.pdf_path, pages_dir)

    ocr_a = get_backend(settings.ocr_a)
    ocr_b = get_backend(settings.ocr_b)

    flags: list[ReviewFlag] = []
    reconciled_pages: list[str] = []
    verbatim_pages: list[str] = []  # faithful primary-OCR text -> original_transcription
    for page_path in page_images:
        page_img = Image.open(page_path)
        # Faintness is judged on the raw scan, before any correction.
        faint = review.assess_image(page_img)
        if faint:
            flags.append(faint)

        upright = preprocess.orient_and_deskew(page_img, settings)
        text_a = ocr_a.transcribe_page(upright).text
        text_b = ocr_b.transcribe_page(upright).text
        verbatim_pages.append(text_a)
        flags.extend(review.assess_ocr(text_a, text_b))

        reconciled = reconcile.reconcile(
            settings,
            transcript_a=text_a,
            transcript_b=text_b,
            page_image=page_path,
            label_a=settings.ocr_a,
            label_b=settings.ocr_b,
        )
        unclear = review.assess_unclear(reconciled)
        if unclear:
            flags.append(unclear)
        reconciled_pages.append(reconciled)

    recipe = structure.structure(
        settings,
        reconciled_pages=reconciled_pages,
        source_pdf=str(job.pdf_path),
        verbatim="\n\n".join(verbatim_pages),
    )
    recipe = enrich_mod.enrich(settings, recipe)

    # Structure + vision-LLM review.
    flags.extend(review.assess_structure(recipe))
    confidence, llm_flags = review.llm_review(settings, recipe, page_images)
    flags.extend(llm_flags)
    report = review.build_report(flags, confidence=confidence)

    # Minor issues ride along as a user-facing note (significant failures are held instead).
    if report.uploadable():
        recipe.import_note = review.format_import_note(report)

    _write_outputs(out_dir, recipe, report, reconciled_pages, source_pdf=job.pdf_path)
    return recipe, report


def _write_outputs(
    out_dir: Path,
    recipe: Recipe,
    report: ReviewReport,
    reconciled_pages: list[str],
    *,
    source_pdf: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "recipe.json").write_text(recipe.model_dump_json(indent=2))
    (out_dir / "review.json").write_text(report.model_dump_json(indent=2))
    (out_dir / "reconciled.txt").write_text("\n\n".join(reconciled_pages))
    # Preserve the original scan alongside the outputs.
    (out_dir / source_pdf.name).write_bytes(source_pdf.read_bytes())


def extract_all(settings: Settings) -> None:
    """Process every PDF in the inbox; write each result under ``output/<slug>/`` for review."""
    jobs = ingest.discover(settings.inbox)
    if not jobs:
        print(f"No PDFs found in {settings.inbox}. Drop one scanned PDF per recipe and retry.")
        return
    for job in jobs:
        print(f"Processing {job.source_name} -> output/{job.slug}/")
        _, report = extract_recipe(settings, job)
        if not report.uploadable():
            note = "  [HELD: significant issues — needs manual review]"
        elif report.status == ReviewStatus.NEEDS_REVIEW:
            note = "  [uploads with Import Software Note]"
        else:
            note = ""
        print(f"  status={report.status}{note}")
