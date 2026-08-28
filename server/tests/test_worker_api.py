"""Integration tests for the worker API (#7) — real Postgres, faked Garage.

Same gating as the other suites: RECIPE_TEST_DB_URL at a scratch database.
"""

from __future__ import annotations

import io
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from recipe_pipeline_server.app import create_app
from recipe_pipeline_server.config import Settings
from sqlalchemy import create_engine, text

DB = os.environ.get("RECIPE_TEST_DB_URL", "")
pytestmark = pytest.mark.skipif(not DB, reason="RECIPE_TEST_DB_URL not set")

HUMAN = {"X-Authentik-Username": "michael"}
KEY = {"X-Worker-Key": "test-worker-key"}
PDF = b"%PDF-1.4 tiny fake scan"

RECIPE = {
    "name": "Wartime Fudge",
    "ingredients": [{"text": "2 cup sugar", "provenance": "from_card"}],
    "instructions": [{"text": "Boil.", "provenance": "from_card"}],
    "original_transcription": "VERBATIM",
}
REVIEW = {"status": "needs_review", "confidence": 0.8,
          "flags": [{"code": "faint_scan", "severity": "warn", "detail": "pale"}]}


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def check(self) -> None:  # pragma: no cover
        pass

    def put_bytes(self, key, data, content_type=None):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


@pytest.fixture
def env():
    settings = Settings(
        _env_file=None, db_url=DB, run_migrations=True,
        s3_access_key="x", s3_secret_key="x", worker_api_key="test-worker-key",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        app.state.storage = FakeStorage()
        sync = create_engine(DB)
        with sync.begin() as conn:
            conn.execute(text("TRUNCATE jobs CASCADE"))
        yield c, sync
        sync.dispose()


def upload_pdf(client, name="card.pdf", data=PDF):
    r = client.post("/api/uploads",
                    files=[("files", (name, io.BytesIO(data), "application/pdf"))],
                    headers=HUMAN)
    return r.json()["results"][0]["job_id"]


def claim(client, worker="w1"):
    return client.post("/api/worker/claim", json={"worker": worker}, headers=KEY)


def submit(client, jid, worker="w1", recipe=RECIPE, review=REVIEW, pages=1):
    return client.post(
        f"/api/worker/jobs/{jid}/result",
        data={"worker": worker, "recipe": json.dumps(recipe), "review": json.dumps(review)},
        files=[("pages", (f"page-{n}.jpg", io.BytesIO(b"\xff\xd8jpg" + bytes([n])), "image/jpeg"))
               for n in range(1, pages + 1)],
        headers=KEY,
    )


def test_auth_required_and_ping_open(env):
    client, sync = env
    assert client.post("/api/worker/claim", json={"worker": "w"}).status_code == 401
    assert client.post("/api/worker/claim", json={"worker": "w"},
                       headers={"X-Worker-Key": "wrong"}).status_code == 401
    assert client.get("/api/worker/ping").status_code == 200  # stays keyless


def test_claim_empty_queue_is_204(env):
    client, sync = env
    assert claim(client).status_code == 204


def test_full_lifecycle_upload_claim_download_submit_review(env):
    client, sync = env
    jid = upload_pdf(client)
    c = claim(client).json()
    assert c["id"] == jid and c["attempts"] == 1

    src = client.get(f"/api/worker/jobs/{jid}/source", headers=KEY)
    assert src.status_code == 200 and src.content == PDF

    r = submit(client, jid, pages=2)
    assert r.status_code == 200 and r.json() == {"status": "needs_review", "already": False}

    # the job is now in the human review queue with its pages served
    q = client.get("/api/queue", headers=HUMAN).json()
    assert [i["id"] for i in q] == [jid]
    assert q[0]["name"] == "Wartime Fudge"
    d = client.get(f"/api/recipes/{jid}", headers=HUMAN).json()
    assert d["pages"] == [1, 2] and d["verbatim"] == "VERBATIM"
    assert client.get(f"/api/recipes/{jid}/pages/2", headers=HUMAN).status_code == 200
    # queue is drained
    assert claim(client).status_code == 204


def test_submit_is_idempotent_on_retry(env):
    client, sync = env
    jid = upload_pdf(client)
    claim(client)
    assert submit(client, jid).json()["already"] is False
    assert submit(client, jid).json()["already"] is True  # retried POST after lost response
    d = client.get(f"/api/recipes/{jid}", headers=HUMAN).json()
    assert d["ingredients"] == ["2 cup sugar"]  # not double-written


def test_expired_lease_is_reclaimed_by_next_claim(env):
    client, sync = env
    jid = upload_pdf(client)
    assert claim(client, "w1").status_code == 200
    with sync.begin() as conn:  # simulate a worker that died mid-job
        conn.execute(text("UPDATE jobs SET lease_expires_at = :t"),
                     {"t": datetime.now(UTC) - timedelta(minutes=1)})
    c2 = claim(client, "w2")
    assert c2.status_code == 200
    assert c2.json()["id"] == jid and c2.json()["attempts"] == 2
    # the dead worker's late submit is refused
    assert submit(client, jid, worker="w1").status_code == 409
    # the live worker's submit lands
    assert submit(client, jid, worker="w2").status_code == 200


def test_fail_path_and_idempotent_fail(env):
    client, sync = env
    jid = upload_pdf(client)
    claim(client)
    r = client.post(f"/api/worker/jobs/{jid}/fail",
                    json={"worker": "w1", "error": "ocr exploded"}, headers=KEY)
    assert r.json() == {"status": "failed", "already": False}
    again = client.post(f"/api/worker/jobs/{jid}/fail",
                        json={"worker": "w1", "error": "again"}, headers=KEY)
    assert again.json()["already"] is True
    d = client.get(f"/api/recipes/{jid}", headers=HUMAN)
    assert d.status_code == 404  # failed upload has no recipe row yet — not reviewable


def test_image_jobs_are_not_claimed_yet(env):
    client, sync = env
    upload_pdf(client, name="photo.heic", data=b"\x00\x00\x00\x18ftypheic" + b"h" * 40)
    assert claim(client).status_code == 204  # image ingest is #11; job stays visibly queued


def test_renew_extends_lease(env):
    client, sync = env
    jid = upload_pdf(client)
    first = claim(client).json()["lease_expires_at"]
    r = client.post(f"/api/worker/jobs/{jid}/renew", json={"worker": "w1"}, headers=KEY)
    assert r.status_code == 200 and r.json()["lease_expires_at"] >= first
    assert client.post(f"/api/worker/jobs/{jid}/renew",
                       json={"worker": "intruder"}, headers=KEY).status_code == 409


def test_bad_result_payload_is_422_and_job_stays_processing(env):
    client, sync = env
    jid = upload_pdf(client)
    claim(client)
    r = client.post(
        f"/api/worker/jobs/{jid}/result",
        data={"worker": "w1", "recipe": "{not json", "review": json.dumps(REVIEW)},
        headers=KEY,
    )
    assert r.status_code == 422
    assert submit(client, jid).status_code == 200  # a corrected retry still lands


def test_failed_review_verdict_files_job_as_failed(env):
    """A worker result whose OWN review says `failed` lands in failed, not the human queue."""
    client, sync = env
    jid = upload_pdf(client)
    claim(client)
    bad_review = {"status": "failed", "confidence": 0.1,
                  "flags": [{"code": "empty_ocr", "severity": "fail", "detail": "no text found"}]}
    r = submit(client, jid, review=bad_review)
    assert r.json() == {"status": "failed", "already": False}
    assert submit(client, jid, review=bad_review).json()["already"] is True  # still idempotent
    assert client.get("/api/queue", headers=HUMAN).json() == []
    s = client.get("/api/status", headers=HUMAN).json()
    assert s["counts"] == {"failed": 1}
