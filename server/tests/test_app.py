"""App-factory and probe-contract tests: no Postgres or Garage needed.

The central guarantee here is that readiness is INDEPENDENT of the backing stores. With a
single replica, failing readiness on a slow dependency removes the only Service endpoint and
turns a degraded dependency into a hard 503 — so /readyz must stay green while /statusz is
the one that reports (and bounds) dependency health.
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient
from recipe_pipeline_server.app import ALEMBIC_INI, create_app
from recipe_pipeline_server.config import Settings


def _test_settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        run_migrations=False,
        # Both point at closed localhost ports: every dependency check must fail.
        db_url="postgresql+psycopg://nobody:nope@127.0.0.1:1/recipes",
        s3_endpoint="http://127.0.0.1:1",
        s3_access_key="x",
        s3_secret_key="x",
        **overrides,
    )


def test_alembic_ini_is_where_the_app_expects():
    assert ALEMBIC_INI.is_file(), "app.py's ALEMBIC_INI path drifted from server/alembic.ini"


def test_healthz_never_touches_a_dependency():
    with TestClient(create_app(_test_settings())) as client:
        assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_stays_green_when_backends_are_down():
    """The regression guard: dependency outage must NOT deschedule the only replica."""
    with TestClient(create_app(_test_settings())) as client:
        r = client.get("/readyz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_statusz_reports_unreachable_backends():
    with TestClient(create_app(_test_settings())) as client:
        r = client.get("/statusz")
        assert r.status_code == 503
        body = r.json()
        assert body["db"].startswith("error")
        assert body["s3"].startswith("error")


def test_statusz_is_bounded_by_dep_check_timeout():
    """A wedged dependency must surface as a prompt timeout, never a hung request."""
    app = create_app(_test_settings(dep_check_timeout=0.2))

    class WedgedStorage:
        def check(self) -> None:
            time.sleep(1.5)  # far longer than the budget

    with TestClient(app) as client:
        app.state.storage = WedgedStorage()
        started = time.time()
        r = client.get("/statusz")
        elapsed = time.time() - started

    assert r.status_code == 503
    assert "timeout" in r.json()["s3"]
    assert elapsed < 1.0, f"/statusz took {elapsed:.2f}s — the wait_for guard is not applied"


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("RECIPE_DB_URL", "postgresql+psycopg://u:p@example:5432/recipes")
    monkeypatch.setenv("RECIPE_GROUP_TAGS", "A, B ,C")
    s = Settings(_env_file=None)
    assert s.db_url.endswith("example:5432/recipes")
    assert s.group_tag_list == ["A", "B", "C"]
