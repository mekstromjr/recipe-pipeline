"""End-to-end extraction: one recipe PDF -> a reviewed :class:`Recipe` written to ``output/``.

This is the orchestration glue. The early stages (discover, render) are real; the per-page
stages call the (currently stubbed) processing modules, so running it today exercises the wiring
and stops at the first unimplemented stage with a clear message.

Flow:  ingest -> render -> preprocess -> segment -> OCR x2 -> reconcile -> structure -> enrich.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from recipe_pipeline import enrich as enrich_mod
from recipe_pipeline import ingest, pdf, preprocess, reconcile, segment, structure
from recipe_pipeline.htr import get_backend

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings
    from recipe_pipeline.ingest import RecipeJob
    from recipe_pipeline.schema import Recipe


def extract_recipe(settings: Settings, job: RecipeJob) -> Recipe:
    """Run the full extraction pipeline for one recipe PDF and return the structured recipe."""
    out_dir = settings.outbox / job.slug
    pages_dir = out_dir / "pages"
    page_images = pdf.render_pages_to_dir(job.pdf_path, pages_dir)

    ocr_a = get_backend(settings.ocr_a)
    ocr_b = get_backend(settings.ocr_b)

    reconciled_pages: list[str] = []
    for page_path in page_images:
        from PIL import Image

        page_img = preprocess.orient_and_deskew(Image.open(page_path), settings)
        lines = segment.segment_lines(page_img)
        lines_a = ocr_a.transcribe_lines(lines)
        lines_b = ocr_b.transcribe_lines(lines)
        text_a = "\n".join(line.text for line in lines_a)
        text_b = "\n".join(line.text for line in lines_b)
        reconciled_pages.append(
            reconcile.reconcile(
                settings, transcript_a=text_a, transcript_b=text_b, page_image=page_path
            )
        )

    recipe = structure.structure(
        settings, reconciled_pages=reconciled_pages, source_pdf=str(job.pdf_path)
    )
    recipe = enrich_mod.enrich(settings, recipe)
    return recipe


def extract_all(settings: Settings) -> None:
    """Process every PDF in the inbox; write each result under ``output/<slug>/`` for review."""
    jobs = ingest.discover(settings.inbox)
    if not jobs:
        print(f"No PDFs found in {settings.inbox}. Drop one scanned PDF per recipe and retry.")
        return
    for job in jobs:
        print(f"Processing {job.source_name} -> output/{job.slug}/")
        extract_recipe(settings, job)
