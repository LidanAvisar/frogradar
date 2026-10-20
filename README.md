# FrogRadar

AI-powered competitive intelligence for JFrog. A daily pipeline collects industry signals (blogs, release
feeds, GitHub, pricing-page changes), enriches and verifies them with LLMs, and stores everything in one
data model that powers the Morning Brief, battlecards, cited chat, research missions and an MCP server.

> Status: foundation in place (FastAPI + Postgres/pgvector schema, validated config, seed, LLM gateway).
> Collectors and the daily pipeline are next.

## Quick start

Requirements: Docker, [uv](https://docs.astral.sh/uv/), Node 20 (frontend).

```bash
cp .env.example .env        # fill in API keys (never commit .env)
make up                     # Postgres + backend; the backend applies migrations on start
make seed                   # load competitors, sources and capabilities from config/
curl localhost:8000/health
```

## Commands

| Command | What it does |
|---|---|
| `make up` / `make down` | Start / stop everything with docker compose |
| `make db-up` | Start only Postgres and wait until healthy |
| `make migrate` | Apply Alembic migrations (`backend/app/db/migrations`) |
| `make seed` | Upsert entities, sources, capabilities from `config/` (safe to re-run) |
| `make test` | Backend tests, incl. migrations and seed on a real throwaway Postgres DB (LLM calls faked) |
| `make test-live` | Tests marked `live`: real provider calls (needs keys in `.env`, costs < 1 cent). The only way they run. |
| `make llm-smoke` | One tiny call per LLM tier + one embedding; prints model, latency, tokens, cost |
| `make lint` | ruff check + format check |

## Configuration (`config/`)

All "what to track" settings are YAML, validated by Pydantic on load (`backend/app/core/catalog.py`):

- `competitors.yaml` – companies (incl. JFrog as `self`), relationship, aliases, segments, products, sources
- `sources.yaml` – industry sources not tied to one competitor
- `capabilities.yaml` – capability taxonomy by segment (artifact management, supply-chain security, MLOps, CI/CD, distribution)
- `llm_routing.yaml` – model per task (bulk / synthesis / verify), embedding model + dimension, fallback pricing

Adding a competitor = add a block to `competitors.yaml`, then `make seed`.

## Data model

`entities` (companies/products) · `sources` · `signals` (one event: summary, why JFrog cares, impact,
confidence, evidence quote, verification, embedding) · `signal_entities` · `insights` + `insight_signals` ·
`feedback` (analyst actions) · `missions` + `mission_steps` · `capabilities` + `capability_matrix` ·
`page_snapshots` · `watch_topics` · `pipeline_runs` · `llm_calls` (tokens, cost, latency, prompt version) ·
`llm_cache` (validated LLM outputs and embeddings by content hash; also feeds demo mode) · `reports`
(stored briefs, battlecards, digests). Models: `backend/app/db/models/`.

## LLM gateway (`backend/app/core/llm/`)

Every LLM and embedding call goes through one module (LiteLLM underneath):

```python
from app.core.llm import complete, embed, llm_context, load_prompt

prompt = load_prompt("enrich_signal")              # app/prompts/enrich_signal.v<N>.md (latest)
with llm_context(run_id=run.id):                   # run/mission id recorded on every call
    out = complete("bulk", "enrich_signal", prompt.messages(text=...), EnrichedSignal,
                   prompt_version=prompt.id)       # validated Pydantic object
vectors = embed(["..."]).vectors
```

Task tiers (`bulk` / `synthesis` / `verify`) map to models in `config/llm_routing.yaml` (env overrides:
`LLM_MODEL_*`). Each call is cached by content hash, logged to `llm_calls` with tokens/cost/latency, and checked
against `LLM_DAILY_BUDGET_USD` (and a mission's budget). With `DEMO_MODE=true` nothing reaches a provider:
responses come from `llm_cache`, and a miss raises `DemoCacheMiss`.

## Design decisions

Each line: what we chose – why – what we rejected.

- **D1 uv + multi-stage Docker** – lockfile-reproducible installs, small non-root runtime image – rejected pip/requirements.txt and single-stage images.
- **D2 DB URL inside compose** – compose points the backend at host `db`; Postgres bound to 127.0.0.1 only – rejected a second env file and exposing 5432 on the LAN.
- **D3 /health semantics** – `SELECT 1`; 200 ok / 503 degraded so probes and UI can both use it.
- **D4 Enums as VARCHAR + CHECK** – Python enums, easy to extend in a migration – rejected native PG enums (`ALTER TYPE`, type cleanup on downgrade).
- **D5 Embeddings: text-embedding-3-small, 1536 dims** – cheap, strong, under pgvector's 2000-dim HNSW limit; dimension lives in config and is checked against the DB at seed/startup. Changing it = migration + full re-embed – rejected 3-large (3072 dims, 6× cost).
- **D6 BIGINT identity keys, timestamptz everywhere** – simple and readable – rejected UUIDs (no multi-writer need).
- **D7 Config mounted into the container, migrations on start** – edit YAML + reseed without rebuilding; `make up` always gives a migrated DB. Since S3 the image also carries config as defaults (D14).
- **D8 Tests on throwaway Postgres DBs** – real pgvector/CHECK behavior, dev data untouched; skip if no DB, fail in CI (`REQUIRE_DB=1`) – rejected SQLite and testcontainers.
- **D9 Capabilities are a taxonomy table; entity segments is an array** – one source of truth; vendors span several segments – rejected capability-as-entity and single segment.
- **D10 content_hash = sha256 of normalized text** (HTML stripped, whitespace collapsed, lowercased) – exact dedupe across markup changes before paying for embeddings – rejected raw-HTML or URL-only dedupe.
- **D11 Seed ownership** – upsert by natural key, update only changed config fields, never overwrite analyst-owned fields, deactivate (not delete) removed sources – rejected truncate-and-reload.
- **D12 LLM routing** – verify model must come from a different provider than both writers; env vars can override, result always validated – rejected env-only routing.
- **D13 Entity linking hygiene** – relationship (self/competitor/partner/coopetition); no broad "Amazon/Microsoft/Google" aliases, only product aliases – rejected treating everyone as a plain competitor.
- **D14 Config baked into the image** – repo-root build context with an allow-list `.dockerignore`; `/app/config` is the default, compose mounts `./config` over it in dev – so the image runs standalone (Artifactory, demo mode) – rejected mount-only and `--build-context`.
- **D15 One LLM gateway** (`app/core/llm`) – every LLM/embedding call goes through `complete()` / `embed()`; structured output via `response_format` or JSON-in-text, up to 2 validation retries with the error fed back; backoff on 429/5xx, no retry on 4xx; one `llm_calls` row per attempt (incl. cache hits and failures), written in its own transaction – rejected LiteLLM's built-in retries (invisible attempts) and an async-only API.
- **D16 Content-hash cache** – sha256 of model + params + messages + response schema + `CACHE_VERSION`; embeddings cached per text; only validated outputs are cached; hits cost 0 – rejected caching raw provider responses and per-batch embedding keys.
- **D17 Unknown cost = NULL, never 0** – LiteLLM price → `pricing` in `llm_routing.yaml` → NULL + warning; cost stored with 10 decimals (an embedding call is ~1e-7 USD) – rejected silently recording 0.
- **D18 Budget guard** – before each provider call: today's (UTC) spend vs `LLM_DAILY_BUDGET_USD` → `BudgetExceeded`; mission spend vs `missions.budget_usd` → `MissionBudgetExceeded`; callers decide what to do – the crossing call may overshoot slightly – rejected pre-call cost estimates.
- **D19 Versioned prompt files** – `app/prompts/<name>.v<N>.md`, `$var` placeholders; `prompt_version` stored on every call so evals map to prompt versions – rejected prompts in code and Jinja.
- **D20 LiteLLM supply chain** – `litellm>=1.106.0,!=1.82.7,!=1.82.8` (versions compromised on PyPI in March 2026); `uv.lock` carries sha256 hashes; Docker installs with `uv sync --frozen` – rejected unpinned latest.
- **D21 Tests can't reach a provider** – LiteLLM never auto-loads `.env` or downloads its price table (`LITELLM_MODE=PRODUCTION`, `LITELLM_LOCAL_MODEL_COST_MAP=True`); non-live tests run with provider keys removed and `litellm.completion/embedding` replaced by a failing guard; live tests need `-m live` and `RUN_LIVE_TESTS=1` (`make test-live`) – rejected relying on the marker alone.

## Repository layout

```
backend/   FastAPI app (api, core, db, pipeline, agents, mcp, demo) + tests
frontend/  Angular 18 app
config/    competitors, sources, capabilities, LLM routing (YAML)
eval/      labeled evaluation set
```
