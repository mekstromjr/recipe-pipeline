"""End-to-end extraction: scanned recipe PDFs -> reviewed recipe output under ``output/``.

Per-recipe flow:  render -> preprocess (orient/deskew) -> OCR (Apple Vision) -> structure ->
enrich -> review. Significant failures are HELD; minor issues upload with an Import Software Note.
No Mealie upload (recipes are bulk-imported manually). A run logs per-stage progress to the
console and ``output/run.log`` and writes a summary ``output/_manifest.json``.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from PIL import Image

from recipe_pipeline import enrich as enrich_mod
from recipe_pipeline import ingest, pdf, preprocess, render, review, structure
from recipe_pipeline.htr import get_backend
from recipe_pipeline.schema import ReviewFlag

if TYPE_CHECKING:
    from pathlib import Path

    from recipe_pipeline.config import Settings
    from recipe_pipeline.ingest import RecipeJob
    from recipe_pipeline.schema import Recipe, ReviewReport

logger = logging.getLogger("recipe_pipeline")


@contextmanager
def _stage(name: str):
    """Log a stage's start and elapsed time."""
    logger.info("    %-12s ...", name)
    t = time.perf_counter()
    yield
    logger.info("    %-12s done (%.1fs)", name, time.perf_counter() - t)


def _setup_logging(out_root: Path) -> None:
    """Log INFO to the console and to output/run.log (idempotent)."""
    out_root.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    if any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        return
    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")
    fh = logging.FileHandler(out_root / "run.log")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)


def extract_recipe(
    settings: Settings, job: RecipeJob, *, ocr_backend: str | None = None
) -> tuple[Recipe, ReviewReport]:
    """Run the full pipeline for one recipe PDF; return the recipe and its review report.

    ``ocr_backend`` overrides the OCR engine (used by ``escalate`` to re-OCR with the cloud model).
    """
    backend_name = ocr_backend or settings.ocr_a
    out_dir = settings.outbox / job.slug
    with _stage("render"):
        page_images = pdf.render_pages_to_dir(job.pdf_path, out_dir / "pages")

    ocr = get_backend(backend_name)
    # Apple Vision returns a 180 page bottom-to-top (reverse it); cloud models auto-orient.
    reverse_180 = backend_name == "applevision"

    flags: list[ReviewFlag] = []
    ocr_pages: list[str] = []
    faint_content_pages = 0
    for n, page_path in enumerate(page_images, 1):
        logger.info("    page %d/%d", n, len(page_images))
        page_img = Image.open(page_path)
        faint = review.is_faint(page_img)
        # OCR the RAW page — Apple Vision reads any orientation and a pre-rotated image OCRs worse.
        with _stage("ocr"):
            result = ocr.transcribe_page(page_img)
        with _stage("orient"):
            deg = preprocess.detect_rotation(settings, page_img)
        lines = list(reversed(result.lines)) if (reverse_180 and deg == 180) else result.lines
        text = "\n".join(ln.text for ln in lines) if lines else result.text
        ocr_pages.append(text)
        # A faint page only matters if it produced text (risk of misread). A faint BLANK page
        # (e.g. the back of a 2-page card) is harmless and must not fail the recipe.
        if faint and len(text.strip()) >= review.EMPTY_OCR_CHARS:
            faint_content_pages += 1
        # Persist an upright page for the review pass + Mealie display image (NOT for OCR).
        preprocess.make_upright(page_img, deg).convert("RGB").save(page_path, "JPEG", quality=92)

    # Empty only if the WHOLE scan is unreadable — a single blank page among good ones is fine.
    flags.extend(review.assess_ocr("\n".join(ocr_pages)))
    if faint_content_pages:
        flags.append(
            ReviewFlag(
                code="faint_scan",
                severity="warn",
                detail=f"{faint_content_pages} faint page(s) with text — verify reading",
            )
        )

    with _stage("structure"):
        recipe = structure.structure(
            settings, ocr_pages=ocr_pages, source_pdf=str(job.pdf_path),
            verbatim="\n\n".join(ocr_pages),
        )
    with _stage("enrich"):
        recipe = enrich_mod.enrich(settings, recipe)
    recipe.page_images = [p.name for p in page_images]

    flags.extend(review.assess_structure(recipe))
    unclear = review.assess_unclear(_recipe_text(recipe))
    if unclear:
        flags.append(unclear)
    with _stage("review"):
        confidence, llm_flags = review.llm_review(settings, recipe, page_images)
    flags.extend(llm_flags)
    report = review.build_report(flags, confidence=confidence)

    if report.uploadable():
        recipe.import_note = review.format_import_note(report)

    _write_outputs(out_dir, recipe, report, ocr_pages, source_pdf=job.pdf_path)
    return recipe, report


def _recipe_text(recipe: Recipe) -> str:
    """Combined recipe text, for scanning [UNCLEAR] markers the structure stage may have left."""
    parts = [recipe.name or ""]
    parts += [i.text for i in recipe.ingredients]
    parts += [s.text for s in recipe.instructions]
    return "\n".join(parts)


def _write_outputs(
    out_dir: Path, recipe: Recipe, report: ReviewReport, ocr_pages: list[str],
    *, source_pdf: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "recipe.json").write_text(recipe.model_dump_json(indent=2))
    (out_dir / "recipe.md").write_text(render.recipe_to_markdown(recipe))
    (out_dir / "review.json").write_text(report.model_dump_json(indent=2))
    (out_dir / "ocr.txt").write_text("\n\n".join(ocr_pages))
    (out_dir / source_pdf.name).write_bytes(source_pdf.read_bytes())


def extract_all(settings: Settings, *, limit: int | None = None, force: bool = False) -> None:
    """Process inbox/*.pdf; log progress and write output/<slug>/ + a manifest.

    Resumable: recipes already extracted (output/<slug>/recipe.json exists) are skipped unless
    ``force``. ``limit`` caps how many *new* recipes are processed (for a sample batch).
    """
    _setup_logging(settings.outbox)
    jobs = ingest.discover(settings.inbox)
    if not jobs:
        logger.info("No PDFs found in %s. Drop a scanned PDF per recipe and retry.", settings.inbox)
        return

    total = len(jobs)
    if not force:
        def _done(j) -> bool:
            return (settings.outbox / j.slug / "recipe.json").exists()

        n_done = sum(1 for j in jobs if _done(j))
        jobs = [j for j in jobs if not _done(j)]
        if n_done:
            logger.info("Skipping %d already-extracted recipe(s) (use --force to redo)", n_done)
    if limit is not None:
        jobs = jobs[:limit]

    if not jobs:
        logger.info("Nothing to do (%d total, all already extracted).", total)
        return
    logger.info("Starting run: %d of %d recipe(s)", len(jobs), total)
    run_start = time.perf_counter()
    # Accumulate into the existing manifest so it stays cumulative across nightly runs.
    manifest_path = settings.outbox / "_manifest.json"
    by_slug: dict[str, dict] = {}
    if manifest_path.exists():
        by_slug = {e["slug"]: e for e in json.loads(manifest_path.read_text())}
    run_entries: list[dict] = []
    for i, job in enumerate(jobs, 1):
        logger.info("[%d/%d] %s -> output/%s/", i, len(jobs), job.source_name, job.slug)
        t = time.perf_counter()
        entry: dict = {"slug": job.slug, "source": job.source_name}
        try:
            recipe, report = extract_recipe(settings, job)
            entry.update(
                status=str(report.status), confidence=report.confidence,
                uploadable=report.uploadable(),
                ingredients=len(recipe.ingredients), steps=len(recipe.instructions),
                flags=[f.code for f in report.flags],
            )
            held = "HELD" if not report.uploadable() else "ok-to-upload"
            logger.info("  -> %s (%s) %.1fs", report.status, held, time.perf_counter() - t)
        except Exception as exc:  # noqa: BLE001 - isolate failures so the batch continues
            entry.update(status="error", error=f"{type(exc).__name__}: {exc}")
            logger.exception("  -> ERROR on %s: %s", job.source_name, exc)
        by_slug[job.slug] = entry
        run_entries.append(entry)

    manifest_path.write_text(json.dumps(list(by_slug.values()), indent=2))
    _log_summary(run_entries, time.perf_counter() - run_start)


def escalate(settings: Settings, *, limit: int | None = None) -> None:
    """Re-OCR the FAILED recipes in the manifest with the cloud vision model and update outputs.

    The only stage that sends scans off-device — and only for the failed tail that local OCR
    couldn't read. Reads the preserved source PDF from each output/<slug>/ directory.
    """
    _setup_logging(settings.outbox)
    manifest_path = settings.outbox / "_manifest.json"
    if not manifest_path.exists():
        logger.info("No manifest at %s — run `extract` first.", manifest_path)
        return
    manifest = json.loads(manifest_path.read_text())
    by_slug = {e["slug"]: e for e in manifest}
    failed = [e for e in manifest if e.get("status") == "failed"]
    if limit is not None:
        failed = failed[:limit]
    if not failed:
        logger.info("No failed recipes to escalate.")
        return

    logger.info("Escalating %d failed recipe(s) to %s", len(failed), settings.openrouter_ocr_model)
    run_start = time.perf_counter()
    for i, e in enumerate(failed, 1):
        slug = e["slug"]
        src = settings.outbox / slug / e["source"]
        if not src.exists():
            logger.warning("  [%d/%d] %s: source PDF missing, skipping", i, len(failed), slug)
            continue
        logger.info("[%d/%d] escalating %s", i, len(failed), slug)
        t = time.perf_counter()
        try:
            recipe, report = extract_recipe(
                settings, ingest.RecipeJob(pdf_path=src, slug=slug), ocr_backend="openrouter"
            )
            by_slug[slug].update(
                status=str(report.status), confidence=report.confidence,
                uploadable=report.uploadable(),
                ingredients=len(recipe.ingredients), steps=len(recipe.instructions),
                flags=[f.code for f in report.flags], escalated=True,
            )
            logger.info("  -> %s (%.1fs)", report.status, time.perf_counter() - t)
        except Exception as exc:  # noqa: BLE001 - isolate failures so the batch continues
            logger.exception("  -> ERROR escalating %s: %s", slug, exc)

    updated = list(by_slug.values())
    manifest_path.write_text(json.dumps(updated, indent=2))
    _log_summary(updated, time.perf_counter() - run_start)


def _log_summary(manifest: list[dict], elapsed: float) -> None:
    counts: dict[str, int] = {}
    for e in manifest:
        counts[e.get("status", "?")] = counts.get(e.get("status", "?"), 0) + 1
    logger.info("=" * 56)
    logger.info("Run complete: %d recipe(s) in %.0fs", len(manifest), elapsed)
    for status, n in sorted(counts.items()):
        logger.info("  %-13s %d", status, n)
    held = [e["slug"] for e in manifest if e.get("status") in ("failed", "error")]
    if held:
        logger.info("Held / errored (need manual attention): %s", ", ".join(held))
    logger.info("Manifest: output/_manifest.json")
