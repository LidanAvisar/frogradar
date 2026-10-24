# Operations: commands, configuration, data model

<!-- Moved out of the README (S17). The README links here; keep both in sync. -->

How to run, configure and maintain FrogRadar. Quick start: [README](../README.md#quick-start).

## Running FrogRadar

The commands are in the [README quick start](../README.md#quick-start). The details:

- **Keys.** `make up` checks the two required keys first (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` with the default
  routing) and stops with one message naming a missing key and where to put it, e.g. *"FrogRadar can't start:
  missing OPENAI_API_KEY (for verify openai/gpt-5.4-mini, embedding openai/text-embedding-3-small). Add it to .env
  in the repository root (copy .env.example to .env if you haven't), then run `make up` again."* A missing optional
  key turns off only its feature, and the UI says so: without `TAVILY_API_KEY`, research missions search
  FrogRadar's own data only; without `GITHUB_TOKEN`, GitHub is limited to 60 requests/hour; without `MCP_TOKEN`,
  MCP over HTTP is off. Key values are never logged, returned by the API or sent to the web app.
- **Seed.** `make seed` loads the committed intelligence (signals, battlecards, the radar, two finished missions,
  past Ask conversations) as normal data, without LLM calls, so every screen has content before the first pipeline
  run. It only loads into a fresh database (details: [Seed data](#seed-data)).
- **Older data.** The app uses the real date. When nothing is newer than the last 7 days (for example only the
  seed), the Brief shows the latest available day under the header *LATEST INTELLIGENCE · WEDNESDAY · 21 OCTOBER
  2026* with the note *"Nothing newer yet: this is the latest available day (21 Oct 2026). Run the pipeline (make
  pipeline) for today's news, or make scheduler-up for daily updates."* "Since yesterday" becomes "On 21 Oct
  2026", and the top story is that day's. A daily brief older than a day is never shown as today's top story.
- **Daily updates** cost money, so they're opt-in: `make scheduler-up` starts the scheduler (see
  [Daily scheduler](#daily-scheduler)), `make scheduler-down` stops it. `make down` stops everything; `make status`
  shows keys, features and today's spend.
- **Budget.** `LLM_DAILY_BUDGET_USD` (default $2) is a hard cap per UTC day, checked before every paid call; the UI
  shows today's spend next to it. Each research mission also has its own cap (default $0.30).
- **One provider only?** Route every tier to it with the `LLM_MODEL_*` overrides (see `.env.example`); the required
  keys follow the routing. Because the verifier is then the same model family as the writer, FrogRadar starts only
  with `ALLOW_SAME_FAMILY_VERIFY=true` and warns that accuracy checks are weaker.

## Commands

| Command | What it does |
|---|---|
| `make up` / `make down` | Postgres + backend + web app on :8080 (API on 127.0.0.1:8000; never the scheduler). Checks the required keys first (one message naming a missing key), then prints the key/feature status / stop everything incl. the scheduler |
| `make seed` | Load the committed intelligence into the fresh database of the running stack (no LLM calls; refuses a database in use) |
| `make status` / `make logs` | Keys present, features enabled / degraded / off, today's spend / follow the logs |
| `make seed-export` | Maintainers: export the dev DB as the seed data (raw text stripped, no `llm_cache`; `AS_OF=` to override the as-of time) |
| `make db-up` | Start only Postgres and wait until healthy |
| `make migrate` | Apply Alembic migrations (`backend/app/db/migrations`) |
| `make seed-config` | Upsert entities, sources, capabilities from `config/` (safe to re-run; the backend container also does it at every start) |
| `make test` | Backend tests, incl. migrations and seed on a real throwaway Postgres DB (LLM calls faked) |
| `make test-live` | Tests marked `live`: real provider calls (needs keys in `.env`, costs < 1 cent). The only way they run. |
| `make llm-smoke` | One tiny call per LLM tier + one embedding; prints model, latency, tokens, cost |
| `make collect` | Collect new items from all active sources (`SOURCE="Snyk Blog"` or `SOURCE=12` for one) |
| `make backfill` | Page back through history (`SINCE=90d` default, or `SINCE=2026-01-01`) |
| `make embed` | Embed in-scope signals without an embedding (paid: embedding API, cached per text) |
| `make dedup` | Semantic dedup into clusters (no LLM calls) |
| `make link` | Rule-based entity linking from names/aliases in `competitors.yaml` (no LLM calls) |
| `make dedup-report` | Calibration report: most similar pairs, similarity distribution, entity link counts |
| `make pipeline` | The daily pipeline (LangGraph) in the backend container, paid (first run ≈ $0.25–0.60, then ≈ $0.08 a day): `ARGS="--skip collect"`, `ARGS="--force enrich,verify"` |
| `make brief` | The daily brief only (1 synthesis + 1 verify call, once per UTC day; `ARGS="--force brief"`) |
| `make battlecards` | Battlecards for due competitors (1 synthesis + 1 verify call each; `ARGS="--force battlecards"`) |
| `make radar-backfill` | Competitor Radar discovery for the last 90 days: Hacker News, GitHub new repos, CNCF landscape copies (no LLM calls) |
| `make radar` | Radar step: candidates, emergence score, profiles of the top candidates (paid: bulk + verify; `ARGS="--force radar"`) |
| `make radar-recheck` | Re-apply the radar profile check rule (partial kept + labelled) to stored profiles (no LLM calls) |
| `make mcp` | FrogRadar MCP server over stdio (clients like Claude Desktop start it themselves; see "MCP & Skill") |
| `make mcp-inspector` | MCP Inspector web UI against the stdio server (pinned 2.10.1) |
| `make pipeline-report` | Quality report of the latest run (`RUN=<id>`): triage, distributions, cost per model, samples |
| `make wayback-backfill` | Baseline for page_diff sources: Wayback snapshots ~12/6/3 months back + today, diffed in date order (no LLM calls; `SOURCE=` for one) |
| `make scheduler-up` / `make scheduler-down` | Opt in / out of the daily paid run (`scheduler` compose profile, `DAILY_RUN_CRON`, UTC) |
| `make scheduler-logs` | Follow the scheduler's log |
| `make eval-select` / `eval-pairs` | Eval set (40 signals) / dedup pairs (40, across similarity bands) → `eval/` (no LLM calls) |
| `make eval-embed` | Embed the full catalog for dedup calibration (paid, ~$0.005) |
| `make eval-label` | Silver labels from the synthesis model for the eval set and the pairs (paid, ~$0.70, cached) |
| `make eval` | Eval pass (triage → enrich → verify + verifier thinking A/B) vs silver labels → `eval/results/` (paid for uncached items only; never writes to `signals`) |
| `make eval-ask` | Ask eval (`eval/ask_questions.yaml`): recall@k, cites-expected, not-enough-evidence, cost → `eval/results/ask_*` (paid; `ARGS="--retrieval-only"` = embeddings only; `--cap 0.60`) |
| `make eval-compare A=… B=…` | Headline metrics of two eval runs side by side |
| `make eval-gold` | Analyst-reviewed signals → `eval/gold_labels.csv` (eval format, no LLM); `make eval ARGS="--labels eval/gold_labels.csv"` scores the pipeline against them |
| `make api-types` | Export the OpenAPI schema and regenerate the frontend's TypeScript types (commit both) |
| `make screenshots` | Playwright screenshots of the Brief, Radar list, Radar detail, Battlecard (Sonatype) and Signals (filtered) at 1440 / 390 px with the local Google Chrome (API on :8000, `ng serve` on :4200, `FRONTEND_URL` / `API_URL` to override) |
| `make lint` | ruff check + format check |

## Configuration

Code: `config/`

All "what to track" settings are YAML, validated by Pydantic on load (`backend/app/core/catalog.py`):

- `competitors.yaml` – companies (incl. JFrog as `self`), relationship, aliases (with optional match rules for
  ambiguous names), segments, products (a product can be marked `discontinued`), sources
- `sources.yaml` – industry sources not tied to one competitor
- `capabilities.yaml` – capability taxonomy by segment (artifact management, supply-chain security, MLOps, CI/CD, distribution)
- `jfrog_capabilities.yaml` – JFrog's own level per capability for the battlecard matrix, each with a note and the
  public JFrog doc it comes from; `unknown` where no doc clearly states it (never from the LLM)
- `llm_routing.yaml` – model per task (bulk / synthesis / verify), embedding model + dimension, fallback pricing
- `pipeline.yaml` – embedding input, dedup window and thresholds, LLM parallelism, text limits per stage,
  confidence weights / caps / bands, daily brief window, battlecard refresh rules
- `discovery.yaml` – Competitor Radar: discovery sources and their queries (Hacker News, GitHub search, CNCF
  categories), the GitHub noise filter, the ignore list, merge thresholds, emergence-score weights, profile limits

Adding a competitor = add a block to `competitors.yaml`, then `make seed`.

## Data model

`entities` (companies/products) · `sources` · `signals` (one event: summary, why JFrog cares, impact,
confidence, evidence quote, verification, embedding) · `signal_entities` · `insights` + `insight_signals` ·
`feedback` (analyst actions) · `missions` + `mission_steps` · `capabilities` + `capability_matrix` ·
`page_snapshots` · `watch_topics` · `pipeline_runs` · `llm_calls` (tokens, cost, latency, prompt version) ·
`llm_cache` (validated LLM outputs and embeddings by content hash) · `reports`
(stored briefs, battlecards, digests) · `radar_candidates` (discovered players: mentions, sources, metrics,
score + breakdown, profile, status, merges) · `repo_star_snapshots` (GitHub stars per repo per day).
Models: `backend/app/db/models/`.

## Source scope

The pipeline runs on **3 curated sources plus 2 watched pricing pages** (cost control); the full catalog stays in `config/`:
**13 competitors** (plus JFrog itself) and **47 sources**, every other source set to `is_active: false`.

| Source | Why it's active |
|---|---|
| Sonatype Blog | Direct competitor in artifact management and supply-chain security (Nexus) |
| GitHub Changelog | High-frequency changelog of a major platform: steady stream of concrete product changes |
| The New Stack | Industry press: mentions many vendors, so it exercises entity linking and cross-source dedup |
| Sonatype Pricing (page diff) | The closest competitor's price list: tiers, editions, consumption pricing |
| GitHub Pricing (page diff) | Plans and add-ons (Advanced Security, Actions minutes, Packages storage) of the largest platform |

To widen the scope, set `is_active: true` on a source (or remove the flag) and restart the backend (`docker compose
restart backend`; the config is re-applied at every start); nothing needs
to be deleted or re-added. Signals already collected from inactive sources stay in the DB, but later
pipeline stages (dedup, enrichment, verification) only process signals whose source is active
(`app/pipeline/scope.py`).

## Daily scheduler

The `scheduler` compose service (`backend/app/scheduler.py`, APScheduler) runs the whole graph once a day at
`DAILY_RUN_CRON` (UTC, default `0 6 * * *`). **It is opt-in**, because every run makes paid LLM calls: it sits in
the compose profile `scheduler` and starts only with `make scheduler-up` (= `docker compose --profile scheduler
up -d scheduler`). Plain `make up` / `docker compose up` never starts it; `make scheduler-down` stops it. Missed runs collapse into one. Only one pipeline run happens at a time:
`make pipeline`, the scheduler and the Wayback backfill all take the same Postgres advisory lock, and a second run
exits without starting. If the process dies, the lock is released with its connection. The daily LLM budget
(`LLM_DAILY_BUDGET_USD`) still applies: once it is reached, the remaining LLM stages are skipped and the run is
marked `partial`. `make pipeline` keeps working by hand.

## LLM gateway

Code: `backend/app/core/llm/`

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
against `LLM_DAILY_BUDGET_USD` (and a mission's budget).

## Seed data

Code: `backend/app/seed/`

`make seed` loads `backend/app/seed/data/` (`data.tar.gz` + `manifest.json`) into the running stack's database as
normal data: signals with their enrichment, battlecards, the capability matrix, the radar, two finished research
missions, past Ask conversations, reviews and the cost history (`llm_calls`). No LLM call is made, and the app
reads the rows with the real clock (the Brief shows the latest available day with its date when nothing is newer).

- one CSV per table (`COPY`), generated columns rebuilt on load; the manifest records the as-of time (the latest
  finished pipeline run), the migration and row counts. The load refuses a database that holds more than config
  rows ("start over: `make down`, `docker compose down -v`, `make up`, `make seed`") or another migration
  ("re-export"); afterwards it re-applies `config/`;
- stripped: `signals.raw_text`, `page_snapshots.text`, `mission_sources.excerpt` (never shown) and embeddings of
  signals outside the active sources; `llm_cache` is not loaded. Loaded rows are flagged `meta.snapshot_stripped`:
  the pipeline never re-processes them, and a pricing page without text starts a new baseline instead of a diff.

**Updating it** (after new data or a migration): `make seed-export` (no LLM calls), then commit
`backend/app/seed/data/`. `make test` fails while the committed seed is at another migration than the code.

## Development setup

Backend with uv (Python 3.12), frontend with Node 20+:

```bash
make db-up && make seed-config     # Postgres on :5432, entities / sources / capabilities from config/
cd backend && uv run uvicorn app.main:app --reload    # API on :8000 (keys: uv run --env-file ../.env …)
cd frontend && npm ci && npm start                    # http://localhost:4200, proxy to :8000
make test && make lint                                # backend; frontend: npm run test:ci, npm run lint:tokens
```
