"""Integration tests for the upload intake (#6) — real Postgres, faked Garage.

Same gating as test_review_api: set RECIPE_TEST_DB_URL at a scratch database.
"""

from __future__ import annotations

import io
import os
import zipfile

import pytest
from fastapi.testclient import TestClient
from recipe_pipeline_server.app import create_app
from recipe_pipeline_server.config import Settings
from sqlalchemy import create_engine, text

DB = os.environ.get("RECIPE_TEST_DB_URL", "")
pytestmark = pytest.mark.skipif(not DB, reason="RECIPE_TEST_DB_URL not set")

AUTH = {"X-Authentik-Username": "cousin-jen"}

PDF = b"%PDF-1.4 tiny fake pdf"
JPG = b"\xff\xd8\xff\xe0" + b"j" * 40
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 40
HEIC = b"\x00\x00\x00\x18ftypheic" + b"h" * 40
TXT = b"just some text, not a scan"


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def check(self) -> None:  # pragma: no cover
        pass

    def put_bytes(self, key, data, content_type=None):
        self.objects[key] = data


@pytest.fixture
def client():
    settings = Settings(
        _env_file=None, db_url=DB, run_migrations=True,
        s3_access_key="x", s3_secret_key="x",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        app.state.storage = FakeStorage()
        sync = create_engine(DB)
        with sync.begin() as conn:
            conn.execute(text("TRUNCATE jobs CASCADE"))
        sync.dispose()
        yield c


def post(client, *files):
    return client.post(
        "/api/uploads",
        files=[("files", (name, io.BytesIO(data), "application/octet-stream"))
               for name, data in files],
        headers=AUTH,
    )


def test_upload_requires_auth(client):
    r = client.post(
        "/api/uploads", files=[("files", ("a.pdf", io.BytesIO(PDF), "application/pdf"))]
    )
    assert r.status_code == 401


def test_single_pdf_becomes_queued_job(client):
    r = post(client, ("grandmas-pie.pdf", PDF))
    assert r.status_code == 200, r.text
    (res,) = r.json()["results"]
    assert res["status"] == "queued" and res["job_id"]
    # the job is visible in status counts, stamped with the uploader
    s = client.get("/api/status", headers=AUTH).json()
    assert s["counts"] == {"queued": 1}
    # source object was stored under the job's key
    storage = client.app.state.storage
    assert any(res["job_id"] in k and k.endswith("grandmas-pie.pdf") for k in storage.objects)


def test_type_sniffing_not_extension(client):
    # a text file wearing a .pdf name must be rejected; a real HEIC under .bin accepted
    r = post(client, ("fake.pdf", TXT), ("photo.bin", HEIC))
    a, b = r.json()["results"]
    assert a["status"] == "error" and "not a PDF" in a["detail"]
    assert b["status"] == "queued"


def test_zip_batch_expands_with_junk_and_bad_members(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("scans/card1.pdf", PDF)
        z.writestr("scans/card2.jpg", JPG)
        z.writestr("__MACOSX/scans/._card1.pdf", b"apple junk")
        z.writestr(".DS_Store", b"junk")
        z.writestr("notes.txt", TXT)
    r = post(client, ("batch.zip", buf.getvalue()))
    results = r.json()["results"]
    by_name = {x["filename"]: x["status"] for x in results}
    assert by_name == {"card1.pdf": "queued", "card2.jpg": "queued", "notes.txt": "error"}
    # junk entries vanish silently — not even error rows
    s = client.get("/api/status", headers=AUTH).json()
    assert s["counts"] == {"queued": 2}


def test_duplicate_content_reported_not_requeued(client):
    assert post(client, ("a.pdf", PDF)).json()["results"][0]["status"] == "queued"
    res = post(client, ("same-thing-renamed.pdf", PDF)).json()["results"][0]
    assert res["status"] == "duplicate"
    assert "a.pdf" in res["detail"]
    s = client.get("/api/status", headers=AUTH).json()
    assert s["counts"] == {"queued": 1}


def test_oversize_file_rejected(client, monkeypatch):
    import recipe_pipeline_server.api.uploads as up
    monkeypatch.setattr(up, "MAX_FILE_BYTES", 10)
    res = post(client, ("big.pdf", PDF)).json()["results"][0]
    assert res["status"] == "error" and "larger than" in res["detail"]


def test_nested_zip_rejected(client):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("x.pdf", PDF)
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr("inner.zip", inner.getvalue())
    res = post(client, ("outer.zip", outer.getvalue())).json()["results"][0]
    assert res["filename"] == "inner.zip" and res["status"] == "error"
