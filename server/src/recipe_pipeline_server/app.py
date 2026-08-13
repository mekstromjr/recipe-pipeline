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
from sqlalchemy import text

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
        app.state.engine = make_engine(settings.db_url)
        app.state.sessionmaker = make_sessionmaker(app.state.engine)
        app.state.storage = Storage(settings)
        # Indirection so tests can swap in a transport-mocked client.
        from recipe_pipeline_server.mealie import MealieClient

        app.state.mealie_client_factory = lambda: MealieClient(settings)
        yield
        await app.state.engine.dispose()

    app = FastAPI(title="recipe-service", lifespan=lifespan)

    from recipe_pipeline_server.api.review import router as review_router

    app.include_router(review_router)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(response: Response) -> dict:
        checks: dict[str, str] = {}
        try:
            async with app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["db"] = "ok"
        except Exception as exc:  # pragma: no cover - message shape only
            checks["db"] = f"error: {exc.__class__.__name__}"
        try:
            await asyncio.to_thread(app.state.storage.check)
            checks["s3"] = "ok"
        except Exception as exc:
            checks["s3"] = f"error: {exc.__class__.__name__}"
        if any(v != "ok" for v in checks.values()):
            response.status_code = 503
        return checks

    return app
