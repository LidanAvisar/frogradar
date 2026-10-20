# Decisions

## D1 – Backend packaging: uv + multi-stage Docker (S1)
- **What:** `backend/` is a uv project (`pyproject.toml` + committed `uv.lock`). The Docker image builds the venv
  with `uv sync --frozen --no-dev` in a builder stage and copies it into `python:3.12-slim` running as a non-root user.
- **Why:** reproducible installs from the lockfile, small runtime image without build tooling, least privilege.
- **Rejected:** pip + requirements.txt (no lockfile resolution), single-stage image (larger, ships uv/build cache).

## D2 – DB connection inside compose (S1)
- **What:** compose sets the backend's `DATABASE_URL` from `POSTGRES_*` with host `db`; `POSTGRES_*` fall back to
  the `.env.example` defaults when unset. Postgres is published on `127.0.0.1:5432` only.
- **Why:** `.env`'s `DATABASE_URL` targets `localhost` for host-side tools (tests, Alembic); inside the compose network
  the host is `db`. Binding to 127.0.0.1 keeps the dev DB off the LAN.
- **Rejected:** a second env file just for Docker (duplication); exposing 5432 on all interfaces.

## D3 – /health semantics (S1)
- **What:** `GET /health` runs `SELECT 1`; returns 200 `{status: ok, db: connected}` or 503 `{status: degraded, db: disconnected}`.
- **Why:** usable by Docker/K8s probes and by the UI, which still renders the body on 503.
