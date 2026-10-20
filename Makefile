.PHONY: up down logs db-up migrate seed test test-live lint llm-smoke

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

# Start only Postgres and wait until it is healthy.
db-up:
	docker compose up -d --wait db

migrate: db-up
	cd backend && uv run alembic upgrade head

# Upsert entities, sources and capabilities from config/ (idempotent).
seed: migrate
	cd backend && uv run python -m app.db.seed

test: db-up
	cd backend && uv run pytest

# Real provider calls (costs a few cents). LiteLLM reads keys from the environment, so pass .env.
test-live: db-up
	cd backend && RUN_LIVE_TESTS=1 uv run --env-file ../.env pytest -m live

# One tiny call per LLM tier + one embedding; prints model, latency and cost per call.
llm-smoke: migrate
	cd backend && uv run --env-file ../.env python -m app.core.llm.smoke

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
