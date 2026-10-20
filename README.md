# FrogRadar

AI-powered competitive intelligence for JFrog. A daily pipeline collects industry signals (blogs, release
feeds, GitHub, pricing-page changes), enriches and verifies them with LLMs, and stores everything in one
data model that powers the Morning Brief, battlecards, cited chat, research missions and an MCP server.

> Status: foundation in place (FastAPI + Postgres/pgvector schema, validated config, seed). Collectors and
> LLM features are next.

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
| `make test` | Backend tests, incl. migrations and seed on a real throwaway Postgres DB |
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
`page_snapshots` · `watch_topics` · `pipeline_runs` · `llm_calls` (tokens, cost, latency) · `reports`
(stored briefs, battlecards, digests). Models: `backend/app/db/models/`.

## Design decisions

Each line: what we chose – why – what we rejected.

- **D1 uv + multi-stage Docker** – lockfile-reproducible installs, small non-root runtime image – rejected pip/requirements.txt and single-stage images.
- **D2 DB URL inside compose** – compose points the backend at host `db`; Postgres bound to 127.0.0.1 only – rejected a second env file and exposing 5432 on the LAN.
- **D3 /health semantics** – `SELECT 1`; 200 ok / 503 degraded so probes and UI can both use it.
- **D4 Enums as VARCHAR + CHECK** – Python enums, easy to extend in a migration – rejected native PG enums (`ALTER TYPE`, type cleanup on downgrade).
- **D5 Embeddings: text-embedding-3-small, 1536 dims** – cheap, strong, under pgvector's 2000-dim HNSW limit; dimension lives in config and is checked against the DB at seed/startup. Changing it = migration + full re-embed – rejected 3-large (3072 dims, 6× cost).
- **D6 BIGINT identity keys, timestamptz everywhere** – simple and readable – rejected UUIDs (no multi-writer need).
- **D7 Config mounted into the container, migrations on start** – edit YAML + reseed without rebuilding; `make up` always gives a migrated DB – rejected baking config into the image.
- **D8 Tests on throwaway Postgres DBs** – real pgvector/CHECK behavior, dev data untouched; skip if no DB, fail in CI (`REQUIRE_DB=1`) – rejected SQLite and testcontainers.
- **D9 Capabilities are a taxonomy table; entity segments is an array** – one source of truth; vendors span several segments – rejected capability-as-entity and single segment.
- **D10 content_hash = sha256 of normalized text** (HTML stripped, whitespace collapsed, lowercased) – exact dedupe across markup changes before paying for embeddings – rejected raw-HTML or URL-only dedupe.
- **D11 Seed ownership** – upsert by natural key, update only changed config fields, never overwrite analyst-owned fields, deactivate (not delete) removed sources – rejected truncate-and-reload.
- **D12 LLM routing** – verify model must come from a different provider than both writers; env vars can override, result always validated – rejected env-only routing.
- **D13 Entity linking hygiene** – relationship (self/competitor/partner/coopetition); no broad "Amazon/Microsoft/Google" aliases, only product aliases – rejected treating everyone as a plain competitor.

## Repository layout

```
backend/   FastAPI app (api, core, db, pipeline, agents, mcp, demo) + tests
frontend/  Angular 18 app
config/    competitors, sources, capabilities, LLM routing (YAML)
eval/      labeled evaluation set
```
