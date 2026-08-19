"""Integration tests for the review API against a real Postgres.

Skipped unless RECIPE_TEST_DB_URL is set — point it at a SCRATCH database (schema is
migrated on app startup and tables are truncated per test). Run e.g.:

    kubectl -n infra port-forward svc/postgres 5433:5432 &
    RECIPE_TEST_DB_URL="postgresql+psycopg://recipes:<pw>@localhost:5433/recipes_test" \
        uv run pytest server/tests/test_review_api.py

Garage and Mealie are faked (in-memory storage / httpx.MockTransport) — this suite is about
the API + FSM + JSONB behavior, which needs real Postgres semantics.
"""

from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from recipe_pipeline_server.app import create_app
from recipe_pipeline_server.config import Settings
from recipe_pipeline_server.db.models import Asset, Job, RecipeRow
from recipe_pipeline_server.mealie import MealieClient
from recipe_pipeline_server.storage import page_key, source_key
from recipe_pipeline_shared.jobs import JobStatus, SourceKind
from recipe_pipeline_shared.schema import ReviewFlag, ReviewReport, ReviewStatus
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DB = os.environ.get("RECIPE_TEST_DB_URL", "")
pytestmark = pytest.mark.skipif(not DB, reason="RECIPE_TEST_DB_URL not set")

AUTH = {"X-Authentik-Username": "michael"}


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.bucket = "recipes"

    def check(self) -> None:  # pragma: no cover
        pass

    def put_bytes(self, key, data, content_type=None):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


def fake_mealie(settings, *, fail: bool = False) -> MealieClient:
    """MealieClient wired to an in-process fake of the v3.19.1 surface we use."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if fail and request.method == "POST" and path == "/api/recipes":
            return httpx.Response(500, text="boom")
        if path.startswith("/api/organizers/"):
            if request.method == "GET":
                return httpx.Response(200, json={"items": []})
            name = json.loads(request.content)["name"]
            return httpx.Response(
                200, json={"id": "x", "name": name, "slug": name.lower().replace(" ", "-")}
            )
        if request.method == "POST" and path == "/api/recipes":
            return httpx.Response(200, json="lemon-sauce")
        # PATCH recipe, PUT image, POST assets
        return httpx.Response(200, json={})

    return MealieClient(settings, transport=httpx.MockTransport(handler))


@pytest.fixture
def env():
    settings = Settings(
        _env_file=None,
        db_url=DB,
        run_migrations=True,
        mealie_api_token="test-token",
        s3_access_key="x",
        s3_secret_key="x",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        storage = FakeStorage()
        app.state.storage = storage
        app.state.mealie_client_factory = lambda: fake_mealie(settings)
        sync = create_engine(DB)
        with sync.begin() as conn:
            conn.execute(text("TRUNCATE jobs CASCADE"))
        yield client, app, storage, sync
        sync.dispose()


def seed_job(sync, storage, *, name="Lemon Sauce", confidence=0.9, status=JobStatus.NEEDS_REVIEW):
    job_id = uuid.uuid4()
    recipe = {
        "name": name,
        "description": "old family favorite",
        "ingredients": [{"text": "1 cup sugar", "provenance": "from_card"}],
        "instructions": [{"text": "Boil it.", "provenance": "from_card"}],
        "original_transcription": "VERBATIM TEXT",
    }
    review = ReviewReport(
        status=ReviewStatus.NEEDS_REVIEW,
        confidence=confidence,
        flags=[ReviewFlag(code="faint_scan", severity="warn", detail="pale ink")],
    )
    with Session(sync) as s:
        s.add(
            Job(
                id=job_id,
                status=status,
                source_kind=SourceKind.PDF,
                source_name=f"{name}.pdf",
                recipe=RecipeRow(data=recipe, review=review.model_dump(mode="json")),
                assets=[
                    Asset(kind="source", s3_key=source_key(str(job_id), f"{name}.pdf")),
                    Asset(kind="page", s3_key=page_key(str(job_id), 1)),
                ],
            )
        )
        s.commit()
    storage.put_bytes(source_key(str(job_id), f"{name}.pdf"), b"%PDF-fake")
    storage.put_bytes(page_key(str(job_id), 1), b"\xff\xd8jpeg-fake")
    return str(job_id)


def test_queue_sorted_and_shaped(env):
    client, app, storage, sync = env
    seed_job(sync, storage, name="Hard One", confidence=0.4)
    easy = seed_job(sync, storage, name="Easy One", confidence=0.95)
    q = client.get("/api/queue").json()
    assert [i["name"] for i in q] == ["Easy One", "Hard One"]
    assert q[0]["id"] == easy
    assert q[0]["flags"] == ["faint_scan"]


def test_detail_edit_roundtrip_preserves_unexposed_fields(env):
    client, app, storage, sync = env
    jid = seed_job(sync, storage)
    d = client.get(f"/api/recipes/{jid}").json()
    assert d["verbatim"] == "VERBATIM TEXT"
    assert d["pages"] == [1]

    r = client.patch(
        f"/api/recipes/{jid}",
        json={"name": "Lemon Sauce (fixed)", "ingredients": ["2 cups sugar", " ", "1 egg"]},
        headers=AUTH,
    )
    assert r.status_code == 200
    d2 = client.get(f"/api/recipes/{jid}").json()
    assert d2["name"] == "Lemon Sauce (fixed)"
    assert d2["ingredients"] == ["2 cups sugar", "1 egg"]
    assert d2["instructions"] == ["Boil it."]  # untouched
    assert d2["verbatim"] == "VERBATIM TEXT"  # unexposed field preserved


def test_edit_requires_auth_header(env):
    client, app, storage, sync = env
    jid = seed_job(sync, storage)
    assert client.patch(f"/api/recipes/{jid}", json={"name": "X"}).status_code == 401


def test_page_image_served(env):
    client, app, storage, sync = env
    jid = seed_job(sync, storage)
    r = client.get(f"/api/recipes/{jid}/pages/1")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content.startswith(b"\xff\xd8")
    # page images are immutable per job -> long-cache, unlike API JSON (no-store)
    assert "immutable" in r.headers["Cache-Control"]
    assert client.get(f"/api/recipes/{jid}").headers["Cache-Control"] == "no-store"


def test_approve_injects_and_uploads(env):
    client, app, storage, sync = env
    jid = seed_job(sync, storage)
    r = client.post(f"/api/recipes/{jid}/approve", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "uploaded"
    assert body["mealie_slug"] == "lemon-sauce"
    assert body["reviewed_by"] == "michael"
    # terminal: a second approve must 409
    assert client.post(f"/api/recipes/{jid}/approve", headers=AUTH).status_code == 409


def test_approve_failure_marks_failed_with_error(env):
    client, app, storage, sync = env
    settings = app.state.settings
    app.state.mealie_client_factory = lambda: fake_mealie(settings, fail=True)
    jid = seed_job(sync, storage)
    r = client.post(f"/api/recipes/{jid}/approve", headers=AUTH)
    assert r.status_code == 502
    d = client.get(f"/api/recipes/{jid}").json()
    assert d["status"] == "failed"
    assert "mealie injection failed" in d["error"]


def test_reject_is_terminal(env):
    client, app, storage, sync = env
    jid = seed_job(sync, storage)
    r = client.post(f"/api/recipes/{jid}/reject", headers=AUTH)
    assert r.json()["status"] == "rejected"
    assert client.patch(f"/api/recipes/{jid}", json={"name": "X"}, headers=AUTH).status_code == 409


def test_status_summary(env):
    client, app, storage, sync = env
    jid = seed_job(sync, storage)
    seed_job(sync, storage, name="Other")
    client.post(f"/api/recipes/{jid}/approve", headers=AUTH)
    s = client.get("/api/status").json()
    assert s["counts"] == {"needs_review": 1, "uploaded": 1}
    assert s["recent"][0]["by"] == "michael"
