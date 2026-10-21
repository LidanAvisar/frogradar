# FrogRadar

AI-powered competitive intelligence for JFrog. A daily pipeline collects industry signals (blogs, release
feeds, GitHub, pricing-page changes), enriches and verifies them with LLMs, and stores everything in one
data model that powers the Morning Brief, battlecards, cited chat, research missions and an MCP server.

> Status: foundation in place (FastAPI + Postgres/pgvector schema, validated config, seed, LLM gateway,
> collectors). Dedup, enrichment and verification are next.

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
| `make collect` | Collect new items from all active sources (`SOURCE="Snyk Blog"` or `SOURCE=12` for one) |
| `make backfill` | Page back through history (`SINCE=90d` default, or `SINCE=2026-01-01`) |
| `make embed` | Embed in-scope signals without an embedding (paid: embedding API, cached per text) |
| `make dedup` | Semantic dedup into clusters (no LLM calls) |
| `make link` | Rule-based entity linking from names/aliases in `competitors.yaml` (no LLM calls) |
| `make dedup-report` | Calibration report: most similar pairs, similarity distribution, entity link counts |
| `make pipeline` | The daily pipeline (LangGraph), paid: `ARGS="--skip collect"`, `ARGS="--force enrich,verify"` |
| `make pipeline-report` | Quality report of the latest run (`RUN=<id>`): triage, distributions, cost per model, samples |
| `make lint` | ruff check + format check |

## Configuration (`config/`)

All "what to track" settings are YAML, validated by Pydantic on load (`backend/app/core/catalog.py`):

- `competitors.yaml` – companies (incl. JFrog as `self`), relationship, aliases (with optional match rules for
  ambiguous names), segments, products, sources
- `sources.yaml` – industry sources not tied to one competitor
- `capabilities.yaml` – capability taxonomy by segment (artifact management, supply-chain security, MLOps, CI/CD, distribution)
- `llm_routing.yaml` – model per task (bulk / synthesis / verify), embedding model + dimension, fallback pricing
- `pipeline.yaml` – embedding input, dedup window and thresholds, LLM parallelism, text limits per stage,
  confidence weights / caps / bands

Adding a competitor = add a block to `competitors.yaml`, then `make seed`.

## Data model

`entities` (companies/products) · `sources` · `signals` (one event: summary, why JFrog cares, impact,
confidence, evidence quote, verification, embedding) · `signal_entities` · `insights` + `insight_signals` ·
`feedback` (analyst actions) · `missions` + `mission_steps` · `capabilities` + `capability_matrix` ·
`page_snapshots` · `watch_topics` · `pipeline_runs` · `llm_calls` (tokens, cost, latency, prompt version) ·
`llm_cache` (validated LLM outputs and embeddings by content hash; also feeds demo mode) · `reports`
(stored briefs, battlecards, digests). Models: `backend/app/db/models/`.

## Demo scope

The demo runs on **3 curated sources**; the full catalog stays in `config/`: **13 competitors** (plus JFrog
itself) and **47 sources**, every other source set to `is_active: false`.

| Source | Why it's in the demo |
|---|---|
| Sonatype Blog | Direct competitor in artifact management and supply-chain security (Nexus) |
| GitHub Changelog | High-frequency changelog of a major platform: steady stream of concrete product changes |
| The New Stack | Industry press: mentions many vendors, so it exercises entity linking and cross-source dedup |

To widen the scope, set `is_active: true` on a source (or remove the flag) and run `make seed`; nothing needs
to be deleted or re-added. Signals already collected from inactive sources stay in the DB, but later
pipeline stages (dedup, enrichment, verification) only process signals whose source is active
(`app/pipeline/scope.py`).

## Collectors (`backend/app/pipeline/collectors/`)

`make collect` reads every active source and stores raw signals (no LLM calls):

- **RSS/Atom** (feedparser). Teaser items (< 500 chars) get the article fetched and the main text extracted
  (trafilatura); articles already stored are not fetched again.
- **GitHub releases** for the repos listed in `options.repos` (REST API; set `GITHUB_TOKEN` for 5000 instead of
  60 requests/hour). Drafts skipped, pre-releases flagged in `signals.meta`.
- **page_diff / web**: stubs for now (page diffs arrive with the page-change watcher).

Good citizenship: identifying User-Agent, robots.txt per host, ≥1s between requests to one host (Crawl-delay
honoured), conditional GET, timeouts. A failing source never stops the run; every run writes a `pipeline_runs`
row with per-source counts (new / updated / duplicate / failed / too old) and errors.

**History limits.** RSS feeds only list their most recent items (often 10–50), so a backfill can't reach further
back than the feed does; GitHub releases page back as far as `--since`. The collect/backfill report says per
source whether it covered the whole window or "only back to <date>". First 90-day backfill over the full catalog
(2026-10-20, before the demo scope was narrowed):

| Coverage | Sources |
|---|---|
| Full 90 days | all 10 GitHub sources; Snyk, Chainguard, Cloudsmith, Hugging Face blogs; GitLab and Artifact Registry release notes |
| ~7 weeks | JFrog Blog (from 2 Sep), Azure DevOps Blog (from 11 Aug) |
| 2–4 weeks | Sonatype Blog, GitLab Blog, Endor Labs Blog, GitHub Blog, Socket Blog |
| Days | GitHub Changelog, The New Stack, InfoQ, BleepingComputer (high-volume feeds list only the latest items) |
| Not collected | Docker, OpenSSF and CNCF blogs block our HTTP client (403); we don't work around bot protection |

Feeds whose items are `#anchors` on one page (release notes) need `options.keep_fragment: true` so each entry
stays its own signal.

**Collected text is for analysis only.** `signals.raw_text` feeds enrichment, verification and search; it is never
shown in the UI or returned verbatim by the API/MCP. Users see a summary, a short evidence quote and a link to
the original.

## Dedup and entity linking (`backend/app/pipeline/`)

After collection, three steps run on in-scope signals (each `step(session, RunContext) -> counts`, one
`pipeline_runs` row per CLI run):

1. **Embed** (`embed.py`) – title + first 2,000 characters of the text, `text-embedding-3-small`, batched.
2. **Dedup** (`dedup.py`) – a signal joins a cluster only if it is similar enough to the cluster's *canonical*
   signal: ≥ 0.85 from another source, ≥ 0.97 from the same source (changelog entries share a template), within
   ±14 days. The canonical is the most trusted source, then the earliest. Duplicates are kept and point at it; the
   canonical lists its sources ("reported by 3 sources"). Re-running changes nothing.
3. **Link** (`link.py`) – names and aliases matched on word boundaries; ambiguous terms (Socket, Docker, GitHub)
   need a capital letter and the title or 3+ mentions; products win over their parent company. Primary = the
   source's own entity or named in the title; otherwise a mention. The LLM confirms/corrects links in enrichment.

First run on the demo scope (56 signals, $0.00043 for embeddings): no cross-source duplicates – the closest
cross-source pair is 0.63, so the three sources don't report the same news yet; the most similar same-source
pair (two GitHub Copilot deprecation notices, 0.89) stays apart as intended. 43 entity links, at most 3 per
signal. `make dedup-report` shows the pairs and the similarity distribution for calibration.

## Daily pipeline: triage, enrichment, verification, confidence

`make pipeline` runs one LangGraph graph (`backend/app/pipeline/graph.py`):

```
collect → embed → dedup → link → triage → enrich → verify → score
```

One `pipeline_runs` row per run with counts per node. A failing node or signal is recorded and the run goes
on (status `partial`); running out of LLM budget skips the remaining LLM stages.

- **Triage** (`triage.v1`, cheap model): is this about JFrog's market (artifact management, supply-chain
  security, MLOps, CI/CD, distribution)? When unsure the answer is yes, because a missed signal costs more than
  an extra enrichment. Irrelevant signals are kept but hidden (`signals.relevant = false`), each with a reason.
- **Enrich** (`enrich_signal.v1`, cheap model): category, 2–3 sentence summary, *why JFrog cares* (naming
  JFrog products; when the news is JFrog's own, framed as positioning against competitors), impact 1–5 against a
  written rubric, an evidence quote, vendor-claim flag, entities, new companies for the Competitor Radar, and
  capabilities. The evidence quote must appear verbatim in the article; this is checked in code, retried once, then
  flagged.
- **Verify** (`verify_signal.v1`, a model from a *different provider*): is the summary supported by the source?
  `supported` / `partial` / `unsupported`, with notes.
- **Score**: confidence is computed in code, never by a model.

**What confidence means:** how faithfully the signal reflects what its source says. It is *not* a judgement of
whether the source's claim is true. Vendor marketing claims carry a separate "vendor claim" label and don't
change confidence.

```
confidence = 0.45·verification + 0.25·source trust + 0.15·corroboration + 0.15·evidence quote
verification: supported 1 · partial 0.5 · unsupported 0      corroboration: 1 source 0 · 2 sources 0.5 · 3+ 1
caps: unsupported ≤ 0.30 · quote not verbatim ≤ 0.60          bands: high ≥ 0.75 · medium ≥ 0.50 · low
```

Weights, caps and bands are in `config/pipeline.yaml`; each signal stores its breakdown in `meta.score`.

**Untrusted input:** article text is wrapped in a block tagged with the text's own hash, so the text can't close the
block. Every prompt says the block is data, not instructions. The pipeline's own checks (verbatim quote, known
entity and capability slugs only, a second model from another provider, confidence in code) hold even if a model
follows injected text, and a test proves it.

**First run** (56 in-scope signals, $0.13 total):
- 26 relevant, 30 irrelevant
- 24 enriched; 2 failed because output was cut off at the old 1,024-token limit
- verification: 23 supported, 1 partial
- confidence: 23 high, 1 medium
- 0 unverified quotes

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
- **D22 One polite collector client** – httpx with identifying User-Agent, robots.txt per host (4xx → allowed, 5xx → stay away; GitHub API exempt), ≥1s per host, redirects checked hop by hop, conditional GET; sources run sequentially and each fails alone; one `pipeline_runs` row with per-source counts – rejected feedparser's own fetching and concurrency for now.
- **D23 Signal identity** – unique (source_id, canonical_url) within a source; same URL with new text updates the signal in place (`meta.revisions`, re-enrich) and counts as "updated"; content_hash (title + text) catches copies across sources; `options.keep_fragment` for feeds of #anchors – rejected content-hash-only identity and insert-only.
- **D24 RSS full text** – fetch and extract (trafilatura) only teaser items, never re-fetch stored articles, keep feed text if extraction fails – rejected always/never fetching articles.
- **D25 raw_text is never shown** – collected text is for analysis; UI/API/MCP show summary + short evidence quote + link – rejected a full-text view (republishing others' content).
- **D26 GitHub repos listed explicitly** – `options.repos` required per GitHub source (15 repos) – rejected scanning whole orgs (API quota, noise).
- **D27 Backfill semantics** – `--since` (default 90d) everywhere; backfill pages GitHub until older than `--since`, RSS gives what the feed lists; report shows real coverage per source – rejected scraping blog archives.
- **D28 No evasion of bot protection** – Docker/OpenSSF/CNCF feeds 403 our client by fingerprint; they stay failed in the run report until there is a sanctioned route – rejected imitating curl/browser fingerprints and silently deactivating them.
- **D29 Demo scope: 3 active sources** – Sonatype Blog (direct competitor), GitHub Changelog (high-frequency product changes), The New Stack (industry press for entity linking and cross-source dedup); all other sources stay in config with `is_active: false`; collected signals from inactive sources are kept but out of scope for dedup/enrichment (`pipeline/scope.py`) – rejected deleting sources from config or signals from the DB.
- **D30 Embedding input** – title + first 2,000 chars of raw_text (`pipeline.yaml`), batched through the gateway; a model change re-embeds and re-clusters – rejected full text (cost, dilution) and embedding summaries (dedup runs before enrichment).
- **D31 Dedup via the canonical** – a signal joins a cluster only at ≥ 0.85 similarity to its canonical signal (≥ 0.97 for the same source, also checked against same-source members), within ±14 days; canonical = highest trust, then earliest; duplicates kept and linked, re-runs change nothing – rejected single-link clustering (chaining), re-clustering every run, deleting duplicates.
- **D32 Rule-based entity linking** – word-boundary matching of names/aliases with per-term rules in `competitors.yaml` (case-sensitive, min mentions, title suffices, disabled), longest match wins so products beat companies, primary = source entity or title, evidence in `meta.entity_links`; the LLM confirms in S6 – rejected LLM-only linking and rules in code.
- **D33 LangGraph daily pipeline** – linear graph collect→…→score reusing the step functions; one run row with counts per node; failures isolated per node and per signal (thread pool, one transaction per signal); budget stop skips later LLM nodes; LangGraph pinned with a 14-day supply-chain cooldown, LangSmith tracing off – rejected a plain loop runner and per-stage transactions.
- **D34 Triage favours recall** – cheap relevance gate per canonical signal, "if unsure, mark relevant"; `signals.relevant` (kept, hidden, not enriched) separate from the analyst's review status; every irrelevant signal listed with its reason – rejected overloading `status` and skipping triage.
- **D35 Checked enrichment** – per-run schema with enum-validated entity/capability slugs; evidence quote verified verbatim in code (one retry, then flagged); the LLM confirms/corrects rule-based entity links; prompt version stored, re-enrich only on a new version or `--force` – rejected free-text entities and trusting the model's quote.
- **D36 Confidence = fidelity to the source, computed in code** – weighted verification, source trust, corroboration and quote check with caps and bands from `pipeline.yaml`; vendor claims flagged separately (`is_claim`) – rejected LLM self-reported confidence and a "truth" score.
- **D37 Prompt-injection defenses** – raw_text in a block tagged with its own hash, "data only" instructions, schema-constrained output, and code checks that hold even if the model obeys the injection – rejected random delimiters (break caching) and stripping instruction-like text.

## Repository layout

```
backend/   FastAPI app (api, core, db, pipeline, agents, mcp, demo) + tests
frontend/  Angular 18 app
config/    competitors, sources, capabilities, LLM routing (YAML)
eval/      labeled evaluation set
```
