"""Command-line entrypoint: ``recipe-pipeline {extract,review,upload,work,bakeoff}``."""

from __future__ import annotations

import argparse
import sys

from recipe_pipeline import __version__
from recipe_pipeline.config import load_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recipe-pipeline",
        description="Scan PDFs of family recipes -> structured recipes -> Mealie.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser(
        "extract", help="Process inbox/*.pdf into reviewed recipe JSON under output/<slug>/."
    )
    p_extract.add_argument(
        "--limit", type=int, default=None, help="Process at most N recipes (for a sample batch)."
    )
    p_extract.add_argument(
        "--force",
        action="store_true",
        help="Reprocess recipes even if output/<slug>/recipe.json already exists.",
    )
    p_extract.set_defaults(func=_cmd_extract)

    p_review = sub.add_parser(
        "review", help="Open the local review UI to work through the needs_review queue."
    )
    p_review.add_argument("--port", type=int, default=8765, help="Port for the review server.")
    p_review.set_defaults(func=_cmd_review)

    p_upload = sub.add_parser(
        "upload", help="Batch-upload reviewed recipes (ok + approved needs_review) to Mealie."
    )
    p_upload.add_argument(
        "--ok-only", action="store_true", help="Upload only `ok` recipes (skip needs_review)."
    )
    p_upload.set_defaults(func=_cmd_upload)

    p_work = sub.add_parser(
        "work", help="Pull loop: claim jobs from the recipe service, extract, submit (#10)."
    )
    p_work.add_argument("--once", action="store_true", help="Process at most one job, then exit.")
    p_work.add_argument("--ocr-backend", default=None, help="Override the OCR engine.")
    p_work.set_defaults(func=_cmd_work)

    p_bakeoff = sub.add_parser(
        "bakeoff", help="Score OCR backends on samples/ (CER/WER) to pick the engine pair."
    )
    p_bakeoff.add_argument(
        "--backends",
        default="glmocr",
        help="Comma-separated backend names to compare (default: glmocr).",
    )
    p_bakeoff.set_defaults(func=_cmd_bakeoff)

    return parser


def _cmd_extract(args: argparse.Namespace) -> int:
    from recipe_pipeline import extract

    extract.extract_all(load_settings(), limit=args.limit, force=args.force)
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    from recipe_pipeline import review_server

    review_server.serve(load_settings(), port=args.port)
    return 0


def _cmd_upload(args: argparse.Namespace) -> int:
    from recipe_pipeline import extract, mealie

    extract._setup_logging(load_settings().outbox)  # console + run.log
    mealie.upload_reviewed(load_settings(), include_needs_review=not args.ok_only)
    return 0


def _cmd_work(args: argparse.Namespace) -> int:
    from recipe_pipeline.config import load_settings
    from recipe_pipeline.pull import PullConfig, run

    run(load_settings(), PullConfig.from_env(), once=args.once, ocr_backend=args.ocr_backend)
    return 0


def _cmd_bakeoff(args: argparse.Namespace) -> int:
    from recipe_pipeline import bakeoff

    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    bakeoff.run_bakeoff(load_settings(), backends=backends)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except NotImplementedError as exc:
        # Stubs raise NotImplementedError; surface it cleanly instead of a traceback.
        print(f"[not yet implemented] {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        # Expected operational errors (e.g. missing token) — clean message, no traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
