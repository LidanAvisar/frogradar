# syntax=docker/dockerfile:1

# Base images are pinned by tag and digest, each at least 14 days old (supply-chain cooldown, D85).
# Builder and runtime use the same Python image, so the venv copied between them matches exactly.

# --- uv binary only (distroless image) ---
FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

# --- Build stage: resolve dependencies into a venv with uv ---
FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY backend/alembic.ini ./
COPY backend/app ./app

# --- Runtime stage: slim image, non-root user ---
FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
# LiteLLM: never load a .env by itself; use its bundled price table (no download on import).
ENV LITELLM_MODE=PRODUCTION LITELLM_LOCAL_MODEL_COST_MAP=True
# The base image's pip (and its bundled ensurepip wheel) is never used: the app's venv has its own
# packages and no pip. Removing it removes its CVEs from the image.
RUN python -m pip uninstall --yes --no-cache-dir pip \
    && rm -rf /usr/local/lib/python3.12/ensurepip/_bundled
RUN groupadd --system app && useradd --system --gid app --no-create-home app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
# Default config baked in, so the image runs on its own (e.g. pulled from Artifactory).
# Owned by root = read-only for the app user. docker-compose mounts ./config over it in dev.
COPY config /app/config
USER app
EXPOSE 8000
# Check the required model keys (one clear message if one is missing), apply migrations, upsert
# competitors / sources / capabilities from config/ (idempotent), then serve. Seed data is loaded
# only on request (make seed).
ENV CONFIG_DIR=/app/config
CMD ["sh", "-c", "python -m app.core.preflight && alembic upgrade head && python -m app.db.seed && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
