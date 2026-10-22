.PHONY: up live down logs status demo demo-down demo-reset demo-logs demo-snapshot demo-prime demo-check \
	db-up migrate seed test test-live lint llm-smoke collect backfill \
	embed dedup link dedup-report brief battlecards pipeline pipeline-report wayback-backfill scheduler-up scheduler-down scheduler-logs \
	eval-select eval-embed eval-pairs eval-label eval eval-compare eval-ask eval-gold api-types screenshots \
	radar radar-backfill radar-recheck mcp mcp-inspector

DEMO_PORT ?= 8080
WEB_PORT ?= 8081
API_PORT ?= 8000

# ---------- Run it (README "Quick start") ----------

# A) Demo: snapshot data, no API keys, no .env. Postgres + backend (DEMO_MODE) + web app (nginx).
demo:
	docker compose --profile demo up -d --build --wait
	@echo ""
	@echo "  FrogRadar demo is running:  http://localhost:$(DEMO_PORT)"
	@echo "  Snapshot data, no API keys needed. Stop: make demo-down"
	@echo ""

demo-down:
	docker compose --profile demo down

# Back to the pristine snapshot (deletes only the demo database volume).
demo-reset: demo-down
	docker volume rm "$$(docker compose --profile demo config | sed -n 's/^name: //p')_pgdata-demo" || true

demo-logs:
	docker compose --profile demo logs -f

# B) Live: your keys from .env (optional; missing keys turn features off). An empty DB is filled
# from the snapshot first. Prints which keys are present and which features are on.
live up:
	docker compose --profile live up -d --build --wait
	@docker compose --profile live exec -T backend python -m app.core.status || true
	@echo ""
	@echo "  FrogRadar is running:  http://localhost:$(WEB_PORT)  (API http://localhost:$(API_PORT))"
	@echo "  Fresh data: make pipeline (paid, see the status above). Stop: make down"
	@echo ""

down:
	docker compose --profile live --profile scheduler down

logs:
	docker compose --profile live logs -f

# Keys present / features enabled or degraded / today's spend (live stack).
status:
	docker compose --profile live exec -T backend python -m app.core.status

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

# The daily pipeline (LangGraph): collect → embed → dedup → link → triage → enrich → verify → score
# → brief → battlecards → radar. Paid (LLM calls; about $0.08 a day on the demo scope). Nodes whose
# keys are missing are skipped. ARGS="--skip collect" / "--force enrich,verify".
# Runs on the host with uv if installed, else inside the live backend container (make live first).
pipeline:
	@if command -v uv >/dev/null 2>&1; then \
		$(MAKE) migrate && cd backend && uv run --env-file ../.env python -m app.pipeline.graph $(ARGS); \
	else \
		docker compose --profile live exec -T backend python -m app.pipeline.graph $(ARGS); \
	fi

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
	docker compose --profile live --profile scheduler up -d --build scheduler

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

# MCP Inspector UI against the stdio server (http://localhost:6274; prints the URL with its token).
# Pinned past the 14-day cooldown; it writes a Claude-Desktop-style config (paths only, no secrets).
INSPECTOR_VERSION ?= 2.10.1
mcp-inspector: db-up
	cfg="$${TMPDIR:-/tmp}/frogradar-mcp-inspector.json" && \
	printf '{"mcpServers":{"frogradar":{"command":"uv","args":["--directory","%s/backend","run","--env-file","%s/.env","frogradar-mcp"]}}}' "$(CURDIR)" "$(CURDIR)" > "$$cfg" && \
	MCP_AUTO_OPEN_ENABLED=false npx -y --legacy-peer-deps -p @modelcontextprotocol/inspector@$(INSPECTOR_VERSION) \
		mcp-inspector --web --config "$$cfg" --server frogradar

# ---------- Demo snapshot (D77). The snapshot lives in backend/app/demo/snapshot/ (committed). ----------
# Ask the curated demo questions (config/demo.yaml) with the clock pinned to the snapshot's as-of
# time, so their answers are in the LLM cache. PAID: about $0.02 per uncached question.
# Prints the estimate only; ARGS=--yes makes the calls.
demo-prime: migrate
	cd backend && uv run --env-file ../.env python -m app.demo.prime $(ARGS)

# Export the dev DB as the demo dataset (raw text stripped). AS_OF=2026-10-21T17:33:00Z to override.
demo-snapshot: migrate
	cd backend && uv run python -m app.demo.snapshot export $(if $(AS_OF),--as-of $(AS_OF))

# Restore the snapshot into a throwaway DB and replay the demo (pages, cached Ask, MCP, 0 provider calls).
demo-check: db-up
	cd backend && uv run python -m app.demo.check
