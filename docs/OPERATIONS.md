# Operations: commands, configuration, data model

<!-- Moved out of the README (S17). The README links here; keep both in sync. -->

How to run, configure and maintain FrogRadar. Quick start: [README](../README.md#quick-start).

## Commands

| Command | What it does |
|---|---|
| `make demo` / `make demo-down` / `make demo-reset` | Demo stack (snapshot, no keys) on :8080 / stop / back to the pristine snapshot |
| `make live` (= `make up`) / `make down` | Live stack (Postgres + backend + web app on :8081; never the scheduler), prints the key/feature status / stop everything incl. the scheduler |
| `make status` | Keys present, features enabled / degraded / off (live stack) |
| `make demo-prime` / `make demo-snapshot` / `make demo-check` | Maintainers: cache the curated Ask answers (paid, `ARGS=--yes`) / export the dev DB as the demo snapshot / replay the demo on a throwaway DB (0 provider calls) |
| `make db-up` | Start only Postgres and wait until healthy |
| `make migrate` | Apply Alembic migrations (`backend/app/db/migrations`) |
| `make seed` | Upsert entities, sources, capabilities from `config/` (safe to re-run) |
| `make test` | Backend tests, incl. migrations and seed on a real throwaway Postgres DB (LLM calls faked) |
| `make test-live` | Tests marked `live`: real provider calls (needs keys in `.env`, costs < 1 cent). The only way they run. |
| `make llm-smoke` | One tiny call per LLM tier + one embedding; prints model, latency, tokens, cost |
| `make collect` | Collect new items from all active sources (`SOURCE="Snyk Blog"` or `SOURCE=12` for one) |
| `make backfill` | Page back through history (`SINCE=90d` default, or `SINCE=2026-01-01`) |
| `make embed` | Embed in-scope signals without an embedding (paid: embedding API, cached per text) |
| `make dedup` | Semantic dedup into clusters (no LLM calls) |
| `make link` | Rule-based entity linking from names/aliases in `competitors.yaml` (no LLM calls) |
| `make dedup-report` | Calibration report: most similar pairs, similarity distribution, entity link counts |
| `make pipeline` | The daily pipeline (LangGraph), paid: `ARGS="--skip collect"`, `ARGS="--force enrich,verify"` |
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
`llm_cache` (validated LLM outputs and embeddings by content hash; also feeds demo mode) · `reports`
(stored briefs, battlecards, digests) · `radar_candidates` (discovered players: mentions, sources, metrics,
score + breakdown, profile, status, merges) · `repo_star_snapshots` (GitHub stars per repo per day).
Models: `backend/app/db/models/`.

## Demo scope

The demo runs on **3 curated sources plus 2 watched pricing pages**; the full catalog stays in `config/`:
**13 competitors** (plus JFrog itself) and **47 sources**, every other source set to `is_active: false`.

| Source | Why it's in the demo |
|---|---|
| Sonatype Blog | Direct competitor in artifact management and supply-chain security (Nexus) |
| GitHub Changelog | High-frequency changelog of a major platform: steady stream of concrete product changes |
| The New Stack | Industry press: mentions many vendors, so it exercises entity linking and cross-source dedup |
| Sonatype Pricing (page diff) | The closest competitor's price list: tiers, editions, consumption pricing |
| GitHub Pricing (page diff) | Plans and add-ons (Advanced Security, Actions minutes, Packages storage) of the largest platform |

To widen the scope, set `is_active: true` on a source (or remove the flag) and run `make seed`; nothing needs
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
against `LLM_DAILY_BUDGET_USD` (and a mission's budget). With `DEMO_MODE=true` nothing reaches a provider:
responses come from `llm_cache`, and a miss raises `DemoCacheMiss`.

## Demo snapshot

Code: `backend/app/demo/`

The demo (`make demo`) restores `backend/app/demo/snapshot/` (`data.tar.gz` + `manifest.json`) into its own
empty database. The snapshot is the dev DB exported with `make demo-snapshot`:

- one CSV per table (`COPY`), generated columns rebuilt on restore; the manifest records the as-of time (the latest
  finished pipeline run), the migration and row counts. Restore refuses a database that already has data
  (`--if-empty` skips quietly) or another migration ("re-export the snapshot");
- stripped: `signals.raw_text`, `page_snapshots.text`, `mission_sources.excerpt` (the app never shows them and
  demo mode makes no LLM calls), embeddings of signals outside the demo scope, and embedding cache rows that
  duplicate `signals.embedding`. Restored rows are flagged `meta.snapshot_stripped`; the live pipeline never
  re-processes them, and a pricing page restored without text starts a new baseline instead of a diff;
- demo mode pins the app's "now" to the as-of time, so the Brief and "since yesterday" look the same on any day.

**Updating it** (after new data, a prompt change or a migration):

```bash
make demo-prime              # estimate: curated Ask questions (config/demo.yaml) not cached yet
make demo-prime ARGS=--yes   # paid: ~$0.02 per uncached question, clock pinned to the as-of time
make demo-snapshot           # export (AS_OF=… to override the as-of time)
make demo-check              # restore into a throwaway DB, replay pages + every curated question + MCP, 0 provider calls
```

`make test` fails while the committed snapshot is at another migration than the code.

## Development setup

Backend with uv (Python 3.12), frontend with Node 20+:

```bash
make db-up && make seed            # Postgres on :5432, entities / sources / capabilities from config/
cd backend && uv run uvicorn app.main:app --reload    # API on :8000 (keys: uv run --env-file ../.env …)
cd frontend && npm ci && npm start                    # http://localhost:4200, proxy to :8000
make test && make lint                                # backend; frontend: npm run test:ci, npm run lint:tokens
```
