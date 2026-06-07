"""Command-line entrypoint: ``recipe-pipeline {extract,upload,bakeoff}``."""

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

    p_escalate = sub.add_parser(
        "escalate",
        help="Re-OCR FAILED recipes with the cloud vision model (better at hard cursive).",
    )
    p_escalate.add_argument(
        "--limit", type=int, default=None, help="Escalate at most N failed recipes."
    )
    p_escalate.set_defaults(func=_cmd_escalate)

    p_upload = sub.add_parser("upload", help="Upload a reviewed recipe (by slug) to Mealie.")
    p_upload.add_argument("slug", help="Slug of the reviewed recipe under output/.")
    p_upload.set_defaults(func=_cmd_upload)

    p_bakeoff = sub.add_parser(
        "bakeoff", help="Score OCR backends on samples/ (CER/WER) to pick the engine pair."
    )
    p_bakeoff.add_argument(
        "--backends",
        default="applevision",
        help="Comma-separated backend names to compare (default: applevision).",
    )
    p_bakeoff.set_defaults(func=_cmd_bakeoff)

    return parser


def _cmd_extract(args: argparse.Namespace) -> int:
    from recipe_pipeline import extract

    extract.extract_all(load_settings(), limit=args.limit, force=args.force)
    return 0


def _cmd_escalate(args: argparse.Namespace) -> int:
    from recipe_pipeline import extract

    extract.escalate(load_settings(), limit=args.limit)
    return 0


def _cmd_upload(args: argparse.Namespace) -> int:
    raise NotImplementedError(
        f"upload not implemented yet (slug={args.slug!r}). See mealie.MealieClient.upload."
    )


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


if __name__ == "__main__":
    raise SystemExit(main())
