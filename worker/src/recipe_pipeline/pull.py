"""Worker pull loop (#10): claim -> download -> extract -> submit, forever.

The worker only ever calls OUTBOUND to the recipe service and holds exactly one
credential, the worker API key. Each claimed job is processed in a throwaway tempdir with
the existing :func:`extract_recipe` pipeline (whatever OCR/LLM engine it is configured
with — the loop is engine-agnostic); results post back idempotently, and any exception
turns into a clean `fail` on the job rather than a stuck lease.

Run: ``recipe-pipeline work [--once] [--ocr-backend NAME]``.

Configuration is read from the environment directly (NOT from Settings — config.py is
mid-rework on a parallel branch; fold these in afterwards):

    RECIPE_SERVER_URL        e.g. https://recipes.meklab.net (required)
    RECIPE_WORKER_API_KEY    the shared worker key (required; Vault secret/prod/recipe-service)
    RECIPE_PULL_POLL_SECONDS idle poll interval (default 60)
    RECIPE_PULL_HOURS        "HH-HH" work window, may wrap midnight (e.g. "22-06"); empty = always
    RECIPE_WORKER_NAME       lease-owner label (default: hostname)
"""

from __future__ import annotations

import logging
import os
import socket
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
from recipe_pipeline_shared.schema import ReviewStatus
from recipe_pipeline_shared.slug import slugify

from recipe_pipeline.config import Settings
from recipe_pipeline.extract import extract_recipe
from recipe_pipeline.ingest import RecipeJob

logger = logging.getLogger("recipe_pipeline")


@dataclass
class PullConfig:
    server_url: str
    api_key: str
    poll_seconds: int = 60
    hours: str = ""
    worker: str = field(default_factory=socket.gethostname)

    @classmethod
    def from_env(cls) -> PullConfig:
        url = os.environ.get("RECIPE_SERVER_URL")
        key = os.environ.get("RECIPE_WORKER_API_KEY")
        if not url or not key:
            raise SystemExit(
                "RECIPE_SERVER_URL and RECIPE_WORKER_API_KEY must be set "
                "(key: Vault secret/prod/recipe-service, property worker-api-key)"
            )
        return cls(
            server_url=url.rstrip("/"),
            api_key=key,
            poll_seconds=int(os.environ.get("RECIPE_PULL_POLL_SECONDS", "60")),
            hours=os.environ.get("RECIPE_PULL_HOURS", ""),
            worker=os.environ.get("RECIPE_WORKER_NAME", socket.gethostname()),
        )


def in_window(hours: str, now: datetime | None = None) -> bool:
    """True if `now` falls inside an "HH-HH" window (wraps midnight); empty = always."""
    if not hours:
        return True
    start_s, _, end_s = hours.partition("-")
    start, end = int(start_s), int(end_s)
    h = (now or datetime.now()).hour
    if start == end:
        return True
    if start < end:
        return start <= h < end
    return h >= start or h < end  # wraps midnight, e.g. 22-06


def _make_client(cfg: PullConfig, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=cfg.server_url,
        headers={"X-Worker-Key": cfg.api_key},
        timeout=httpx.Timeout(300.0, connect=10.0),
        transport=transport,
    )


def process_one(
    http: httpx.Client,
    settings: Settings,
    cfg: PullConfig,
    job: dict,
    *,
    ocr_backend: str | None = None,
) -> str:
    """One claimed job end-to-end. Returns the final status posted to the server."""
    jid = job["id"]
    try:
        src = http.get(f"/api/worker/jobs/{jid}/source")
        src.raise_for_status()
        with tempfile.TemporaryDirectory(prefix="recipe-work-") as td:
            tmp = Path(td)
            pdf_path = tmp / job["source_name"]
            pdf_path.write_bytes(src.content)
            slug = slugify(pdf_path.stem) or jid
            # Point the pipeline's output at the tempdir — nothing lands in output/.
            work_settings = settings.model_copy(update={"output_dir": tmp / "out"})
            rjob = RecipeJob(pdf_path=pdf_path, slug=slug)

            recipe, report = extract_recipe(work_settings, rjob, ocr_backend=ocr_backend)
            # Cloud escalation, same policy as the old nightly: a FAILED local pass gets
            # one shot with the cloud OCR before we accept failure.
            if (
                report.status is ReviewStatus.FAILED
                and ocr_backend is None
                and settings.openrouter_api_key
            ):
                logger.info("[%s] local OCR failed -> escalating to cloud OCR", jid)
                recipe, report = extract_recipe(work_settings, rjob, ocr_backend="openrouter")

            pages = sorted((work_settings.outbox / slug / "pages").glob("page-*.jpg"))
            files = [("pages", (p.name, p.read_bytes(), "image/jpeg")) for p in pages]
            data = {
                "worker": cfg.worker,
                "recipe": recipe.model_dump_json(),
                "review": report.model_dump_json(),
            }
            last_exc: Exception | None = None
            for attempt in range(3):  # idempotent server-side: retries are safe
                try:
                    r = http.post(f"/api/worker/jobs/{jid}/result", data=data, files=files)
                    r.raise_for_status()
                    logger.info("[%s] %s -> needs_review (%d pages, review=%s)",
                                jid, job["source_name"], len(pages), report.status.value)
                    return "needs_review"
                except httpx.HTTPError as exc:
                    last_exc = exc
                    time.sleep(2**attempt)
            raise RuntimeError(f"result submit failed after retries: {last_exc}")
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("[%s] failed: %s", jid, msg)
        try:
            http.post(f"/api/worker/jobs/{jid}/fail",
                      json={"worker": cfg.worker, "error": msg[:2000]})
        except httpx.HTTPError:
            logger.warning("[%s] could not report failure (lease will expire)", jid)
        return "failed"


def run(
    settings: Settings,
    cfg: PullConfig,
    *,
    once: bool = False,
    ocr_backend: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
        )
    http = _make_client(cfg, transport)
    logger.info("worker %s polling %s every %ds (window=%s)",
                cfg.worker, cfg.server_url, cfg.poll_seconds, cfg.hours or "always")
    try:
        while True:
            if not in_window(cfg.hours):
                if once:
                    logger.info("outside work window %s; exiting (--once)", cfg.hours)
                    return
                time.sleep(min(cfg.poll_seconds * 5, 900))
                continue
            try:
                r = http.post("/api/worker/claim", json={"worker": cfg.worker})
            except httpx.HTTPError as exc:
                logger.warning("claim failed (%s); backing off", exc)
                if once:
                    raise
                time.sleep(cfg.poll_seconds)
                continue
            if r.status_code == 204:
                if once:
                    logger.info("queue empty; exiting (--once)")
                    return
                time.sleep(cfg.poll_seconds)
                continue
            r.raise_for_status()
            process_one(http, settings, cfg, r.json(), ocr_backend=ocr_backend)
            if once:
                return
    finally:
        http.close()
