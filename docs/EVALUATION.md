# Evaluation

<!-- Moved out of the README (S17). The README links here; keep both in sync. -->

How FrogRadar's accuracy is measured: the silver-label eval of the pipeline, prompt iteration, gold labels from analysts. The Ask eval is in [FEATURES.md](FEATURES.md#ask-your-intelligence).

`make eval` measures the pipeline against reference labels and writes a report per run to `eval/results/`
(v1: [`…enrich_signal.v1…verify_signal.v1.md`](../eval/results/2026-10-21_triage.v1_enrich_signal.v1_enrich_page_diff.v2_verify_signal.v1.md),
latest: [`…enrich_signal.v2…verify_signal.v2.md`](../eval/results/2026-10-21_triage.v1_enrich_signal.v2_enrich_page_diff.v3_verify_signal.v2.md)).

> **These are agreement scores with a stronger model, not human-verified accuracy.** The reference ("silver")
> labels come from the synthesis model (Claude Sonnet 5.5), which is stronger than the bulk model (Claude Haiku 5.5)
> that runs triage and enrichment, but it is still a model, and from the same provider as the writer.

**How the set was built**
- **40 signals**, stratified from the full catalog of 1,057 (fixed seed, `app/eval/select.py`): 19 from the 3
  demo sources and 21 from inactive sources that the pipeline never processed; items the pipeline had kept and
  dropped; one per category where possible; 3 pricing-page diffs; the known hard cases (#50, #77, #1005, #1053,
  #1055).
- **Silver labels** (`eval/silver_labels.csv`, `label_source` column): relevant, category, impact, primary entity.
  The labeler's prompt (`silver_label.v1`) copies the scope, impact rubric and entity rule from the pipeline's own
  prompts at render time, so both follow the same rules. It never sees a pipeline output. Unlike the pipeline's
  triage it has no "if unsure, mark relevant" bias.
- **40 dedup pairs** (`eval/dedup_pairs.csv`): cross-source pairs within the ±14-day window, sampled across
  0.05-wide similarity bands from 0.60 to 0.95 (the 493 pairs in range thin out fast: 338 below 0.65, 4 above
  0.85). Labeled `same_story` by the same model. Precision and recall are weighted by band size.
- **The eval pass** runs the pipeline's own per-signal functions (`triage_one`, `enrich_one`, `verify_one`),
  so prompts and cache keys are the pipeline's and re-runs are free. Results go to `eval/results/` only; the eval
  never writes to `signals`. Verify runs a second time with the other thinking level for an A/B.
- **No article text in the repo:** the committed files hold ids, titles, URLs, sources, dates, labels and
  model-written reasons only. The labeler and the eval read `raw_text` from the database at run time (the
  [raw_text rule](DESIGN.md#design-decisions), D25).

**Results, first run** (prompts triage.v1, enrich_signal.v1, enrich_page_diff.v2, verify_signal.v1; see
[Prompt iteration](#prompt-iteration-v1--v2) for v2)

| Metric | Value | Notes |
|---|---|---|
| Triage recall | 100% (22/22) | No item the reference called relevant was dropped. |
| Triage precision | 63% | 13 extra items (minor CLI releases, AI-coding opinion pieces): the recall bias at work (D34). |
| Category agreement | 68% (15/22) | Most mismatches: feature_update ↔ product_launch (3). The prompt gives category names only, no definitions. |
| Impact exact / within ±1 | 59% / 100% | Mean signed error +0.23: the pipeline rates slightly higher. |
| Primary entity | 96% strict, 100% counting product ↔ parent company | |
| Evidence quote verbatim | 100% of 35 | 11% needed one retry, 0 code repairs. |
| Verifier partial (thinking low → off) | 51% → 9% | With low, 10 of 18 partials were only about the JFrog-side analysis. |
| Verifier cost and latency (low → off) | $0.102 → $0.066 per 35 items; 2.1 s → 1.1 s median | |
| Dedup cross-source threshold | 0.85: precision 100%, recall 33% | 0.80: precision 87%, recall 78%. |
| Eval cost | $0.93 in total | Embedding $0.004, silver labels $0.71, eval pass $0.21. |

**What changed because of it**
- **Verifier thinking → off (D45).** On the same 35 items, "low" marked 18 partial and "off" marked 3. Reading the
  disagreements: "low" judged the JFrog analysis it is told not to judge (Curation overlap, battlecard advice).
  Its one "factual" catch (#189) was wrong. It found no factual error that "off" missed, and it cost 56% more at
  twice the latency.
- **Dedup threshold stays 0.85, now calibrated (D44).** It is the lowest threshold with precision ≥ 90%. At 0.80
  recall rises to 78%, but the pair at 0.81 is two different vendors' VS Code scanner launches (Endor Labs vs
  Socket). Merging them would hide one competitor's launch, so a missed merge (a duplicate shown twice) is the
  cheaper error.

#### Prompt iteration (v1 → v2)

The first run led to three prompt changes. Two of them address findings from the first run:
- **enrich_signal.v2 / enrich_page_diff.v3:** a one-line definition per category, e.g. product_launch = a new
  product or edition, feature_update = a change to an existing one. v1 listed names only.
- **verify_signal.v2:** JFrog-side analysis is explicitly out of scope, and each unsupported claim names its
  field.

Then the 32 in-scope signals were re-enriched and re-verified (run 30, $0.09) and `make eval` ran again
($0.18; unchanged calls cached). Same 40 items, same silver labels:

| Metric | v1 | v2 |
|---|---|---|
| Category agreement | 68% | 68% |
| Impact exact / within ±1 | 59% / 100% | 64% / 100% |
| Impact mean signed error | +0.23 | +0.27 |
| Primary entity strict / product ↔ company | 96% / 100% | 86% / 100% |
| Evidence quote verbatim | 100% | 97% (1 of 35 unverified, #105) |
| Verifier partial, thinking off (configured) | 9% | 11% (4 partials, all about the summary) |
| Verifier partial, thinking low | 51% | 23% |
| Partials only about JFrog-side analysis (low) | 10 of 18 | 4 of 8 |

What it shows:
- **verify_signal.v2 works where it was needed.** With thinking "low", partials caused by judging the analysis
  fell from 10 to 4. With "off" they were already gone, and they stay gone.
- **The category definitions changed *which* items disagree, not how many.** Some v2 "misses" follow the new
  definitions on purpose (#280: a routine release of an existing open-source tool is now feature_update), while
  the silver labels were made from category names only. Re-labeling with the definitions would make this
  comparison fair (about $0.50). feature_update vs product_launch on "Announcing …" posts (#227, #244) remains
  a judgment call.
- **Entity strict dropped** because v2 more often marks the company rather than the product as primary. At the
  company level both versions agree with the labels 100% of the time.
- One run per version on 40 items: one item moves a metric by 2.5–4.5 points, so only the verifier change is
  clearly larger than noise.

**Limits**
- Small: 40 items, 40 pairs, 10 same-story pairs. One item is worth 2.5 points.
- Single labeler, and a model (silver, not gold). It was set up by the system's builder, and it comes from the
  same provider as the writer, so shared blind spots are possible.
- Stratified, not random. The numbers describe this mix, not the daily feed's. Dedup recall is measured only
  inside [0.60, 0.95).

**Gold labels from daily use:** every signal an analyst approves, corrects or rejects as not relevant (see
"Analyst in the loop") is a gold label. `make eval-gold` writes them to `eval/gold_labels.csv` in the same
format as the silver set (label = the analyst's value, else the model's value the analyst approved;
`label_source = analyst:<name> approve|edit|reject`). Rejections for other reasons (duplicate, outdated) are left
out. `make eval ARGS="--labels eval/gold_labels.csv"` runs the same pass against them, so human-verified
accuracy sits next to the silver agreement. There is one gold label so far (the S14 demo correction).

**Re-running and comparing:** `make eval-select eval-pairs eval-embed eval-label eval` rebuilds everything
(paid steps print calls and cost first; `ARGS=--estimate` prints only). After a prompt change, `make eval` writes
a new file named after the prompt versions; `make eval-compare A=old.json B=new.json` puts the headline metrics
side by side.
