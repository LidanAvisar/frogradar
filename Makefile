.PHONY: up down logs db-up migrate seed test test-live lint llm-smoke collect backfill \
	embed dedup link dedup-report pipeline pipeline-report

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

# Collect raw signals from all active sources (or one: make collect SOURCE="Snyk Blog" / SOURCE=12).
collect: migrate
	cd backend && uv run python -m app.pipeline.collect $(if $(SOURCE),--source "$(SOURCE)")

# Page back through history (GitHub releases; whatever RSS feeds still list). SINCE=90d or YYYY-MM-DD.
backfill: migrate
	cd backend && uv run python -m app.pipeline.collect --backfill --since $(or $(SINCE),90d) $(if $(SOURCE),--source "$(SOURCE)")

# Embed in-scope signals that have no embedding (or one from another model). Paid: embedding API.
embed: migrate
	cd backend && uv run --env-file ../.env python -m app.pipeline.run embed

# Semantic dedup of embedded signals (no LLM calls).
dedup: migrate
	cd backend && uv run python -m app.pipeline.run dedup

# Rule-based entity linking from competitors.yaml names/aliases (no LLM calls).
link: migrate
	cd backend && uv run python -m app.pipeline.run link

# Calibration report: most similar pairs, similarity distribution, entity link counts (no LLM).
dedup-report: migrate
	cd backend && uv run python -m app.pipeline.run report

# The daily pipeline (LangGraph): collect → embed → dedup → link → triage → enrich → verify → score.
# Paid (LLM calls). ARGS="--skip collect" / "--force enrich,verify".
pipeline: migrate
	cd backend && uv run --env-file ../.env python -m app.pipeline.graph $(ARGS)

# Quality report for the latest pipeline run (RUN=<id> for another). No LLM calls.
pipeline-report: migrate
	cd backend && uv run python -m app.pipeline.pipeline_report $(if $(RUN),--run $(RUN))
