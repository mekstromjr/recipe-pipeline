"""Server runtime configuration (env prefix ``RECIPE_``, mirroring the worker's convention).

In k8s every value comes from the ExternalSecret / Deployment env; the defaults here are the
in-cluster service addresses so only credentials need injecting. Locally, point RECIPE_DB_URL
at a port-forward and the rest at localhost forwards.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RECIPE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Postgres (`recipes` DB on the shared infra instance) ---
    # NOTE the +psycopg driver marker: the ESO-templated DSN must include it too, or
    # SQLAlchemy's async engine falls back to a driver we don't ship.
    db_url: str = "postgresql+psycopg://recipes:recipes@localhost:5432/recipes"
    # Run `alembic upgrade head` during startup. Disabled in tests.
    run_migrations: bool = True
    # Hard ceiling on a single Postgres connect attempt (psycopg connect_timeout) and on
    # waiting for a pooled connection. Without these a wedged network path stalls a request
    # indefinitely instead of failing fast.
    db_connect_timeout: int = 5
    db_pool_timeout: int = 5
    # Absolute wall-clock budget for ONE dependency check on /statusz.
    dep_check_timeout: float = 5.0

    # --- Garage S3 (`recipes` bucket; path-style, region `meklab`) ---
    s3_endpoint: str = "http://garage-s3.infra.svc.cluster.local:3900"
    s3_region: str = "meklab"
    s3_bucket: str = "recipes"
    s3_access_key: str = ""
    s3_secret_key: str = ""

    # --- Mealie injection target (#8; plain ClusterIP, no Authentik at the Service) ---
    mealie_url: str = "http://mealie.prod.svc.cluster.local:9000"
    mealie_api_token: str | None = None
    # Fixed tags applied to every injected recipe (filterable batch in Mealie).
    group_tags: str = "a family member,Scan,recipe-pipeline-v1"

    # --- Worker API (#7; the ONE credential the MacBook worker holds) ---
    worker_api_key: str | None = None

    @property
    def group_tag_list(self) -> list[str]:
        return [t.strip() for t in self.group_tags.split(",") if t.strip()]


def load_settings() -> Settings:
    """Construct :class:`Settings`. A function so tests can patch the environment."""
    return Settings()
