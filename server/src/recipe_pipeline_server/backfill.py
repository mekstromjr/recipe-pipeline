"""One-off (but rerunnable) importer: local ``output/<slug>/`` corpus -> DB + Garage (#14).

Run from the Mac that holds the corpus, with port-forwards to Postgres and Garage:

    kubectl -n infra port-forward svc/postgres 5433:5432 &
    kubectl -n infra port-forward svc/garage-s3 3900:3900 &
    RECIPE_DB_URL=... RECIPE_S3_ENDPOINT=http://127.0.0.1:3900 RECIPE_S3_ACCESS_KEY=... \
        uv run python -m recipe_pipeline_server.backfill output --dry-run

Idempotent by ``source_name`` (the output dir name): dirs that already have a job row are
skipped, so re-runs only pick up NEW nightly results — this doubles as the interim "push
to the service" step until the worker pull loop (#10) exists.

Status mapping (review.json + marker files):
    .uploaded marker        -> uploaded (mealie_slug from the marker; do NOT re-inject)
    .rejected marker        -> rejected
    review status ok        -> needs_review (a human still confirms pre-Mealie)
    review needs_review     -> needs_review
    review failed           -> failed (error = joined fail-flag details)
    06072026-105 (HARVARD BEETS) -> failed, explicit corrupted-scan note (ticket #14)
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from recipe_pipeline_shared.jobs import JobStatus, SourceKind
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from recipe_pipeline_server.config import Settings, load_settings
from recipe_pipeline_server.db.models import Asset, Job, RecipeRow
from recipe_pipeline_server.storage import Storage, page_key, source_key

# Known-corrupted scan called out in ticket #14.
CORRUPTED = {"06072026-105": "corrupted scan (HARVARD BEETS) — needs re-scan"}


def map_status(
    dirname: str, review: dict, *, uploaded: bool, rejected: bool
) -> tuple[JobStatus, str | None]:
    """(status, error) for one output dir. Pure — unit-tested."""
    if dirname in CORRUPTED:
        return JobStatus.FAILED, CORRUPTED[dirname]
    if uploaded:
        return JobStatus.UPLOADED, None
    if rejected:
        return JobStatus.REJECTED, None
    status = review.get("status")
    if status == "failed":
        details = "; ".join(
            f["detail"] for f in review.get("flags", [])
            if isinstance(f, dict) and f.get("severity") == "fail"
        )
        return JobStatus.FAILED, details or "extraction failed"
    # `ok` still gets a human glance in the hosted flow before injection.
    return JobStatus.NEEDS_REVIEW, None


def import_dir(d: Path, session: Session, storage: Storage, *, dry_run: bool) -> str:
    review = json.loads((d / "review.json").read_text())
    uploaded = (d / ".uploaded").exists()
    status, error = map_status(
        d.name, review, uploaded=uploaded, rejected=(d / ".rejected").exists()
    )
    pages = sorted((d / "pages").glob("page-*.jpg"))
    pdfs = sorted(d.glob("*.pdf"))
    if dry_run:
        return status.value

    job_id = uuid.uuid4()
    assets = []
    for n, p in enumerate(pages, 1):
        key = page_key(str(job_id), n)
        storage.put_bytes(key, p.read_bytes(), "image/jpeg")
        assets.append(Asset(kind="page", s3_key=key, content_type="image/jpeg"))
    if pdfs:
        key = source_key(str(job_id), pdfs[0].name)
        storage.put_bytes(key, pdfs[0].read_bytes(), "application/pdf")
        assets.append(Asset(kind="source", s3_key=key, content_type="application/pdf"))

    session.add(
        Job(
            id=job_id,
            status=status,
            source_kind=SourceKind.PDF,
            source_name=d.name,
            error=error,
            mealie_slug=(d / ".uploaded").read_text().strip() or None if uploaded else None,
            recipe=RecipeRow(
                data=json.loads((d / "recipe.json").read_text()), review=review
            ),
            assets=assets,
        )
    )
    session.commit()
    return status.value


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("output_dir", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    settings: Settings = load_settings()
    storage = Storage(settings)
    engine = create_engine(settings.db_url)

    dirs = sorted(
        d for d in args.output_dir.iterdir()
        if d.is_dir() and not d.name.startswith("_")
        and (d / "recipe.json").exists() and (d / "review.json").exists()
    )

    counts: dict[str, int] = {}
    imported = skipped = 0
    with Session(engine) as session:
        existing = set(session.execute(select(Job.source_name)).scalars())
        for d in dirs:
            if d.name in existing:
                skipped += 1
                continue
            if args.limit is not None and imported >= args.limit:
                break
            status = import_dir(d, session, storage, dry_run=args.dry_run)
            counts[status] = counts.get(status, 0) + 1
            imported += 1
            if not args.dry_run and imported % 25 == 0:
                print(f"  ... {imported} imported", flush=True)

    mode = "DRY-RUN would import" if args.dry_run else "imported"
    print(f"{mode} {imported} (skipped {skipped} already present) -> {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
