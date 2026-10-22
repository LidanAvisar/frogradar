.PHONY: up down logs db-up migrate seed test test-live lint llm-smoke collect backfill \
	embed dedup link dedup-report brief battlecards pipeline pipeline-report wayback-backfill scheduler-up scheduler-down scheduler-logs \
	eval-select eval-embed eval-pairs eval-label eval eval-compare eval-ask eval-gold api-types screenshots \
	radar radar-backfill radar-recheck mcp mcp-inspector

up:
	docker compose up -d --build

down:
	docker compose --profile scheduler down

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

# Competitor Radar discovery backfill (no LLM): Hacker News + GitHub search for the last 90 days,
# CNCF landscape copies at -90/-60/-30 days (config/discovery.yaml).
radar-backfill: seed
	cd backend && for s in "Hacker News (discovery)" "GitHub new repos (discovery)" "CNCF Landscape (discovery)"; do \
		uv run python -m app.pipeline.collect --backfill --since $(or $(SINCE),90d) --source "$$s" || exit 1; done

# Radar step: candidates, emergence score, profiles of the top candidates (paid: bulk + verify).
# ARGS="--force radar" regenerates profiles.
radar: migrate
	cd backend && uv run --env-file ../.env python -m app.pipeline.run radar $(ARGS)

# Semantic dedup of embedded signals (no LLM calls).
# Re-apply the radar profile check rule to stored profiles (partial kept + labelled). No LLM calls.
radar-recheck: migrate
	cd backend && uv run python -m app.pipeline.radar.recheck

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

# Daily brief (1 synthesis + 1 verify call per day; ARGS="--force brief" to regenerate).
brief: migrate
	cd backend && uv run --env-file ../.env python -m app.pipeline.run brief $(ARGS)

# Battlecards for due competitors (1 synthesis + 1 verify call each; ARGS="--force battlecards").
battlecards: migrate
	cd backend && uv run --env-file ../.env python -m app.pipeline.run battlecards $(ARGS)

# Quality report for the latest pipeline run (RUN=<id> for another). No LLM calls.
pipeline-report: migrate
	cd backend && uv run python -m app.pipeline.pipeline_report $(if $(RUN),--run $(RUN))

# Historical baseline for page_diff sources: Wayback snapshots ~12/6/3 months back + today,
# diffed in date order (no LLM calls; change signals are processed by the next `make pipeline`).
wayback-backfill: migrate
	cd backend && uv run python -m app.pipeline.wayback $(if $(SOURCE),--source "$(SOURCE)") $(if $(ONLY_AFTER),--only-after $(ONLY_AFTER))

# Daily scheduler (opt-in, paid: runs `make pipeline` every day at DAILY_RUN_CRON, UTC).
# `make up` never starts it; this does (compose profile "scheduler").
scheduler-up:
	docker compose --profile scheduler up -d --build scheduler

scheduler-down:
	docker compose --profile scheduler stop scheduler

scheduler-logs:
	docker compose --profile scheduler logs -f scheduler

# ---------- Evaluation (S8, README "Evaluation"). Paid steps print calls + cost first;
# add ARGS=--estimate to print the estimate only. Results: eval/results/.
# Eval set (40 signals, stratified) → eval/silver_labels.csv. No LLM calls.
eval-select: migrate
	cd backend && uv run python -m app.eval select $(ARGS)

# Embed the full catalog (dedup calibration). Paid: embeddings.
eval-embed: migrate
	cd backend && uv run --env-file ../.env python -m app.eval embed $(ARGS)

# Cross-source pairs across similarity bands → eval/dedup_pairs.csv. No LLM calls.
eval-pairs: migrate
	cd backend && uv run python -m app.eval pairs $(ARGS)

# Silver labels (synthesis model) for the eval set and the pairs. Paid; cached.
eval-label: migrate
	cd backend && uv run --env-file ../.env python -m app.eval label $(ARGS)

# Eval pass (triage → enrich → verify, + thinking-off verify) vs silver labels → report.
# Never writes to signals. Paid for uncached items only.
eval: migrate
	cd backend && uv run --env-file ../.env python -m app.eval run $(ARGS)

# Ask eval (eval/ask_questions.yaml): recall@k, cited expected ids, not-enough-evidence, cost.
# Paid (answer + verify per question); ARGS="--retrieval-only" = embeddings only; --cap 0.60 default.
eval-ask: migrate
	cd backend && uv run --env-file ../.env python -m app.eval ask $(ARGS)

# Analyst reviews (approve / correct / reject as not relevant) → eval/gold_labels.csv. No LLM calls.
# Score the pipeline against them: make eval ARGS="--labels eval/gold_labels.csv".
eval-gold: migrate
	cd backend && uv run python -m app.eval gold $(ARGS)

# Compare two runs: make eval-compare A=eval/results/x.json B=eval/results/y.json
eval-compare:
	cd backend && uv run python -m app.eval compare $(abspath $(A)) $(abspath $(B))

# OpenAPI schema from the FastAPI app (no server needed) → generated TypeScript types for the frontend.
api-types:
	cd backend && uv run python -m app.api.openapi_export ../frontend/src/app/api/openapi.json
	cd frontend && npx openapi-typescript src/app/api/openapi.json -o src/app/api/schema.d.ts

# Playwright screenshots of Brief, Radar, Radar detail, Battlecard, Signals and Ask (1440 / 390 px), local Chrome.
# Needs the backend on :8000 and `ng serve` on :4200 (FRONTEND_URL to override).
screenshots:
	cd frontend && node scripts/screenshots.mjs

# FrogRadar MCP server over stdio (Claude Desktop / Cursor / Inspector run this command themselves).
# Needs Postgres (make db-up); keys from .env for `ask` and the search_intel query embedding.
mcp: db-up
	cd backend && uv run --env-file ../.env frogradar-mcp

# MCP Inspector UI against the stdio server (http://localhost:6274). Version pinned past the
# 14-day cooldown.
INSPECTOR_VERSION ?= 2.10.0
mcp-inspector: db-up
	npx -y @modelcontextprotocol/inspector@$(INSPECTOR_VERSION) uv --directory $(CURDIR)/backend run --env-file ../.env frogradar-mcp

