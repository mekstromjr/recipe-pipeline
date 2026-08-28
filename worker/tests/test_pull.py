"""Pull-loop mechanics (#10): claim/download/submit/fail against a transport-mocked server.

The extract stage is faked — engine behavior is the pipeline's own concern; these tests pin
the LOOP's contract: what gets posted where, retries, escalation policy, and the fail path.
"""

from __future__ import annotations

import json
from datetime import datetime

import httpx
import pytest
from recipe_pipeline import pull
from recipe_pipeline.config import Settings
from recipe_pipeline.pull import PullConfig, in_window, process_one, run
from recipe_pipeline_shared.schema import Recipe, ReviewFlag, ReviewReport, ReviewStatus

CFG = PullConfig(server_url="http://server.test", api_key="k", worker="test-worker")
JOB = {"id": "11111111-1111-1111-1111-111111111111", "source_name": "card.pdf",
       "source_kind": "pdf", "attempts": 1}


def make_report(status=ReviewStatus.NEEDS_REVIEW):
    return ReviewReport(status=status, confidence=0.9,
                        flags=[ReviewFlag(code="x", severity="warn", detail="d")])


def fake_extract(calls, *, fail_first_backend=False):
    """Stands in for extract_recipe: records calls, writes one page image."""

    def _extract(settings, job, *, ocr_backend=None):
        calls.append(ocr_backend)
        pages_dir = settings.outbox / job.slug / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        (pages_dir / "page-1.jpg").write_bytes(b"\xff\xd8fake")
        status = (
            ReviewStatus.FAILED
            if fail_first_backend and ocr_backend is None
            else ReviewStatus.NEEDS_REVIEW
        )
        return Recipe(name="Test Card"), make_report(status)

    return _extract


class ServerFake:
    """Minimal in-memory worker-API double; records result/fail posts."""

    def __init__(self, *, result_failures=0):
        self.result_posts = []
        self.fail_posts = []
        self.result_failures = result_failures

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/source"):
            return httpx.Response(200, content=b"%PDF-fake")
        if path.endswith("/result"):
            if self.result_failures > 0:
                self.result_failures -= 1
                return httpx.Response(503, text="flap")
            self.result_posts.append(request)
            return httpx.Response(200, json={"status": "needs_review", "already": False})
        if path.endswith("/fail"):
            self.fail_posts.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "failed", "already": False})
        raise AssertionError(f"unexpected path {path}")


def settings():
    return Settings(_env_file=None, openrouter_api_key=None)


def client_for(server: ServerFake) -> httpx.Client:
    return httpx.Client(base_url=CFG.server_url, transport=httpx.MockTransport(server.handler))


def test_happy_path_posts_result_with_pages(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(pull, "extract_recipe", fake_extract(calls))
    server = ServerFake()
    status = process_one(client_for(server), settings(), CFG, JOB)
    assert status == "needs_review"
    assert calls == [None]
    (post,) = server.result_posts
    body = post.content
    assert b'"Test Card"' in body and b"page-1.jpg" in body
    assert b"test-worker" in body


def test_submit_retries_through_transient_errors(monkeypatch):
    monkeypatch.setattr(pull, "extract_recipe", fake_extract([]))
    monkeypatch.setattr(pull.time, "sleep", lambda s: None)
    server = ServerFake(result_failures=2)  # two 503s, then success
    assert process_one(client_for(server), settings(), CFG, JOB) == "needs_review"
    assert len(server.result_posts) == 1 and not server.fail_posts


def test_extract_exception_posts_fail(monkeypatch):
    def boom(settings, job, *, ocr_backend=None):
        raise RuntimeError("ocr exploded")

    monkeypatch.setattr(pull, "extract_recipe", boom)
    server = ServerFake()
    assert process_one(client_for(server), settings(), CFG, JOB) == "failed"
    (fail,) = server.fail_posts
    assert fail["worker"] == "test-worker" and "ocr exploded" in fail["error"]


def test_failed_local_pass_escalates_to_cloud_once(monkeypatch):
    calls: list = []
    monkeypatch.setattr(pull, "extract_recipe", fake_extract(calls, fail_first_backend=True))
    server = ServerFake()
    s = Settings(_env_file=None, openrouter_api_key="or-key")
    assert process_one(client_for(server), s, CFG, JOB) == "needs_review"
    assert calls == [None, "openrouter"]  # local fail -> exactly one cloud retry


def test_no_escalation_without_key(monkeypatch):
    calls: list = []
    monkeypatch.setattr(pull, "extract_recipe", fake_extract(calls, fail_first_backend=True))
    server = ServerFake()
    assert process_one(client_for(server), settings(), CFG, JOB) == "needs_review"
    assert calls == [None]  # FAILED report still submits — the humans see it in failed later


def test_run_once_exits_on_empty_queue():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/worker/claim"
        assert request.headers["x-worker-key"] == "k"
        return httpx.Response(204)

    run(settings(), CFG, once=True, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("hours", "hour", "expected"),
    [("", 3, True), ("09-17", 10, True), ("09-17", 8, False), ("09-17", 17, False),
     ("22-06", 23, True), ("22-06", 3, True), ("22-06", 12, False), ("07-07", 12, True)],
)
def test_in_window(hours, hour, expected):
    now = datetime(2026, 8, 27, hour, 30)
    assert in_window(hours, now) is expected
