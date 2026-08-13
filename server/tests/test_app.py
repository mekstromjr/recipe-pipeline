"""App-factory smoke tests: no Postgres or Garage needed.

The readiness probe is exercised against closed localhost ports — it must degrade to 503
with per-check detail, not hang or crash (short boto connect timeouts make this fast).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from recipe_pipeline_server.app import ALEMBIC_INI, create_app
from recipe_pipeline_server.config import Settings


def _test_settings() -> Settings:
    return Settings(
        _env_file=None,
        run_migrations=False,
        db_url="postgresql+psycopg://nobody:nope@127.0.0.1:1/recipes",
        s3_endpoint="http://127.0.0.1:1",
        s3_access_key="x",
        s3_secret_key="x",
    )


def test_alembic_ini_is_where_the_app_expects():
    assert ALEMBIC_INI.is_file(), "app.py's ALEMBIC_INI path drifted from server/alembic.ini"


def test_healthz():
    with TestClient(create_app(_test_settings())) as client:
        assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_reports_unreachable_backends():
    with TestClient(create_app(_test_settings())) as client:
        r = client.get("/readyz")
        assert r.status_code == 503
        body = r.json()
        assert body["db"].startswith("error")
        assert body["s3"].startswith("error")


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("RECIPE_DB_URL", "postgresql+psycopg://u:p@example:5432/recipes")
    monkeypatch.setenv("RECIPE_GROUP_TAGS", "A, B ,C")
    s = Settings(_env_file=None)
    assert s.db_url.endswith("example:5432/recipes")
    assert s.group_tag_list == ["A", "B", "C"]
