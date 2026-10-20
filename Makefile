.PHONY: up down logs db-up migrate seed test lint

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

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
