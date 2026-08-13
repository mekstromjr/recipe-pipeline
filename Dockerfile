# recipe-service control plane. Server + shared ONLY — the worker (Apple Vision OCR)
# is macOS-bound and never ships in an image.
FROM python:3.12.11-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.7.17 /uv /uvx /bin/

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

# Workspace manifests first so the third-party dependency layer caches across code changes.
COPY pyproject.toml uv.lock ./
COPY shared/pyproject.toml shared/pyproject.toml
COPY server/pyproject.toml server/pyproject.toml
COPY worker/pyproject.toml worker/pyproject.toml
RUN uv sync --frozen --no-dev --no-install-workspace --package recipe-pipeline-server

# Then the actual packages (editable installs -> server/alembic.ini + migrations stay
# beside the source, where app.run_migrations expects them).
COPY shared/ shared/
COPY server/ server/
RUN uv sync --frozen --no-dev --package recipe-pipeline-server

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "--factory", "recipe_pipeline_server.app:create_app", \
     "--host", "0.0.0.0", "--port", "8000"]
