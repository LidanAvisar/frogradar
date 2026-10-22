# syntax=docker/dockerfile:1

# --- Build stage: resolve dependencies into a venv with uv ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY backend/alembic.ini ./
COPY backend/app ./app

# --- Runtime stage: slim image, non-root user ---
FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
# LiteLLM: never load a .env by itself; use its bundled price table (no download on import).
ENV LITELLM_MODE=PRODUCTION LITELLM_LOCAL_MODEL_COST_MAP=True
RUN groupadd --system app && useradd --system --gid app --no-create-home app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
# Default config baked in, so the image runs on its own (Artifactory, demo mode).
# Owned by root = read-only for the app user. docker-compose mounts ./config over it in dev.
COPY config /app/config
USER app
EXPOSE 8000
# Apply migrations, restore the demo snapshot into an empty DB if asked (compose sets
# SNAPSHOT_RESTORE=if-empty; the image default never touches a DB), then serve.
ENV CONFIG_DIR=/app/config SNAPSHOT_RESTORE=never
CMD ["sh", "-c", "alembic upgrade head && if [ \"$SNAPSHOT_RESTORE\" = if-empty ]; then python -m app.demo.snapshot restore --if-empty; fi && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
