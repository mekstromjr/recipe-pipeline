"""Alembic environment.

The DB URL comes from ``RECIPE_DB_URL`` (the same var the server uses in #4). For offline
SQL generation (`alembic upgrade head --sql`) without a database, a placeholder URL is used —
only the dialect matters there.
"""

from __future__ import annotations

import os

from alembic import context
from recipe_pipeline_server.db.models import Base
from sqlalchemy import engine_from_config, pool

config = context.config

target_metadata = Base.metadata

_OFFLINE_PLACEHOLDER = "postgresql+psycopg://offline/offline"


def _url() -> str:
    return os.environ.get("RECIPE_DB_URL", _OFFLINE_PLACEHOLDER)


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
