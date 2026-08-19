"""FastAPI app factory for the recipe-service control plane.

Run with ``uvicorn --factory recipe_pipeline_server.app:create_app``. The lifespan runs
Alembic migrations (so a fresh pod converges the schema before serving), then builds the
async engine and the S3 client once per process.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse
from sqlalchemy import text

# Module-level import: `from __future__ import annotations` makes hints strings, and FastAPI
# resolves them against module globals — a function-local import silently degrades UserDep
# to an unannotated (query!) parameter.
from recipe_pipeline_server.api.deps import UserDep
from recipe_pipeline_server.config import Settings, load_settings
from recipe_pipeline_server.db.session import make_engine, make_sessionmaker
from recipe_pipeline_server.storage import Storage

logger = logging.getLogger("recipe_service")

# server/alembic.ini — the app runs from the repo checkout (image copies the tree), so the
# ini/migrations live beside the package, not inside it. Guarded by a test.
ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def run_migrations(settings: Settings) -> None:
    """`alembic upgrade head`, in-process. Sync — call via ``asyncio.to_thread``."""
    import os

    from alembic import command
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig(str(ALEMBIC_INI))
    # script_location in the ini is CWD-relative; pin it beside the ini so migrations run
    # no matter where the process was started (uvicorn in the container, pytest at repo root).
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "migrations"))
    # migrations/env.py reads RECIPE_DB_URL.
    os.environ["RECIPE_DB_URL"] = settings.db_url
    command.upgrade(cfg, "head")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.run_migrations:
            logger.info("running migrations")
            await asyncio.to_thread(run_migrations, settings)
        app.state.settings = settings
        app.state.engine = make_engine(
            settings.db_url,
            connect_timeout=settings.db_connect_timeout,
            pool_timeout=settings.db_pool_timeout,
        )
        app.state.sessionmaker = make_sessionmaker(app.state.engine)
        app.state.storage = Storage(settings)
        # Indirection so tests can swap in a transport-mocked client.
        from recipe_pipeline_server.mealie import MealieClient

        app.state.mealie_client_factory = lambda: MealieClient(settings)
        app.state.serving = True
        yield
        # Flip first so in-flight readiness probes see "draining" during graceful shutdown.
        app.state.serving = False
        await app.state.engine.dispose()

    app = FastAPI(title="recipe-service", lifespan=lifespan)

    @app.middleware("http")
    async def cache_headers(request, call_next):
        """Browsers heuristically cache header-less GETs (Safari especially): a PATCHed
        recipe could re-render from stale cached JSON. Page images ARE immutable per job,
        so they get the opposite treatment."""
        response = await call_next(request)
        if request.url.path.startswith("/api"):
            if "/pages/" in request.url.path and response.status_code == 200:
                response.headers["Cache-Control"] = "private, max-age=86400, immutable"
            else:
                response.headers["Cache-Control"] = "no-store"
        return response

    # The worker ingress path (/api/worker, no forward-auth) gets a monitorable root +
    # ping: external uptime checks were 404ing against it, and #7's worker loop will use
    # ping as its connectivity self-test.
    @app.get("/api/worker")
    @app.get("/api/worker/ping")
    async def worker_ping() -> dict:
        return {"status": "ok"}

    from recipe_pipeline_server.api.review import router as review_router

    app.include_router(review_router)

    index = Path(__file__).resolve().parent / "web" / "index.html"

    @app.get("/", include_in_schema=False)
    async def root() -> FileResponse:
        return FileResponse(index)

    @app.get("/api/me")
    async def me(user: UserDep) -> dict:
        """Who forward-auth says we are (shown in the UI, used for audit stamps)."""
        return {"user": user}

    async def _check_db() -> None:
        async with app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def _check_s3() -> None:
        await asyncio.to_thread(app.state.storage.check)

    async def _dependency_checks() -> dict[str, str]:
        """Probe each backing store under an absolute time budget.

        ``wait_for`` is the outer guard: the per-client timeouts (psycopg connect_timeout,
        botocore connect/read) bound individual syscalls, but a pool checkout or a retry
        chain can still stack them. This caps the whole check either way.
        """
        checks: dict[str, str] = {}
        for name, check in (("db", _check_db), ("s3", _check_s3)):
            try:
                await asyncio.wait_for(check(), timeout=settings.dep_check_timeout)
                checks[name] = "ok"
            except TimeoutError:
                checks[name] = f"error: timeout after {settings.dep_check_timeout}s"
            except Exception as exc:
                checks[name] = f"error: {exc.__class__.__name__}"
        return checks

    @app.get("/healthz")
    async def healthz() -> dict:
        """Liveness: the process is up. Never touches a dependency — a restart cannot fix
        a sick database, so a failing dependency must not trigger one."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(response: Response) -> dict:
        """Readiness: can THIS pod serve traffic?

        Deliberately does NOT probe Postgres or Garage. Readiness gates Service endpoint
        membership, which is only useful when there is another replica to route to; with a
        single replica, failing readiness on a briefly-slow dependency removes the only
        backend and turns a degraded dependency into a hard 503 at the ingress. Dependency
        health is reported by /statusz, for monitoring rather than for traffic gating.
        """
        if not getattr(app.state, "serving", False):
            response.status_code = 503
            return {"status": "draining"}
        return {"status": "ok"}

    @app.get("/statusz")
    async def statusz(response: Response) -> dict:
        """Deep dependency check for monitoring/blackbox. NOT wired to a kubelet probe."""
        checks = await _dependency_checks()
        if any(v != "ok" for v in checks.values()):
            response.status_code = 503
        return checks

    return app
