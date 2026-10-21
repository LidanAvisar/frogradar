"""Eval pass: triage → enrich → verify on the eval set through the pipeline's own per-signal
functions (triage_one / enrich_one / verify_one), compared with the silver labels.

- Never writes to signals: results go to eval/results/ only. The prompts and cache keys are the
  pipeline's, so items the pipeline already processed come from the LLM cache (free), and re-runs
  of the eval are free.
- Enrichment follows triage, as in the pipeline: an item triage drops is not enriched (it shows up
  as a triage disagreement instead).
- Verify runs twice per enriched item: the configured thinking level (eval run A) and the
  alternative level (run B: "off" when "low" is configured, else "low"), so llm_calls separates
  the two by run id.
"""

import logging
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.catalog import load_pipeline
from app.core.llm import complete, get_gateway, load_prompt
from app.core.llm.routing import resolve
from app.db.models import Entity, LLMCall, Signal, Source
from app.eval import metrics
from app.eval.common import Estimate, eval_run, parallel_map, probe
from app.eval.files import LabelRow, read_labels, read_pairs
from app.pipeline.enrich import enrich_one, link_roles
from app.pipeline.llm_steps import load_input
from app.pipeline.prompting import Catalog, SignalInput, enrich_messages, triage_messages
from app.pipeline.schemas import TriageResult, enrich_schemas
from app.pipeline.scope import eval_set
from app.pipeline.triage import triage_one
from app.pipeline.verify import verify_one

logger = logging.getLogger(__name__)


def thinking_levels() -> tuple[str, str]:
    """(configured verify thinking, the level it is compared against)."""
    configured = resolve("verify", get_gateway().routing).thinking
    return configured, ("low" if configured == "off" else "off")


def prompt_versions() -> dict[str, str]:
    names = ("triage", "enrich_signal", "enrich_page_diff", "verify_signal")
    return {n: load_prompt(n).id for n in names}


def _inputs(s: Session, ids: list[int]) -> dict[int, tuple[SignalInput, dict, str | None]]:
    rows = s.execute(
        select(Signal.id, Signal.meta, Entity.slug)
        .outerjoin(Source, Source.id == Signal.source_id)
        .outerjoin(Entity, Entity.id == Source.entity_id)
        .where(eval_set(ids))
    ).all()
    return {r.id: (load_input(s, r.id), r.meta or {}, r.slug) for r in rows}


# ---------- estimate ----------


def estimate(factory: sessionmaker[Session]) -> tuple[Estimate, Estimate]:
    """(pipeline pass, verify pass with the alternative thinking level).

    Triage and the first enrich call are probed exactly (cache-aware). Verify depends on the
    enrich output: counted as cached when the item's enrich call is cached and the stored signal
    was verified with the same prompt, else priced from the enrich input size (upper bound:
    assumes every item is enriched)."""
    cfg = load_pipeline()
    rows = read_labels()
    ids = [r.id for r in rows]
    gw = get_gateway()
    verify_model = resolve("verify", gw.routing).model
    with factory() as s:
        catalog = Catalog.load(s)
        inputs = _inputs(s, ids)
        verified = dict(
            s.execute(
                select(Signal.id, Signal.meta["verify"]["prompt_version"].astext).where(
                    eval_set(ids)
                )
            ).all()
        )
    schemas = enrich_schemas(catalog.entity_slugs, catalog.capability_slugs)
    verify_version = load_prompt("verify_signal").id
    main, off = Estimate(), Estimate()
    from app.eval.common import count_tokens
    from app.pipeline.page_diff import is_page_diff

    for i in ids:
        sig, meta, _ = inputs[i]
        _, tmsg = triage_messages(sig, catalog, cfg.triage.max_chars)
        probe(main, "bulk", tmsg, TriageResult, tokens_out=80)
        name = "enrich_page_diff" if is_page_diff(meta) else "enrich_signal"
        _, emsg = enrich_messages(sig, catalog, cfg.enrich.max_chars, name)
        schema = schemas.pick(is_page_diff(meta))
        enrich_cached = gw.cached("bulk", emsg, schema)
        probe(main, "bulk", emsg, schema, tokens_out=700)
        vin = count_tokens(verify_model, emsg) // 2 + 600  # source text + summary, no catalog
        cached = enrich_cached and verified.get(i) == verify_version
        main.add(verify_model, vin, 600, cached)
        off.add(verify_model, vin, 250 if thinking_levels()[1] == "off" else 600, False)
    return main, off


# ---------- the pass ----------


def _process(
    row: LabelRow,
    inputs: dict[int, tuple[SignalInput, dict, str | None]],
    catalog: Catalog,
    schemas: Any,
) -> dict[str, Any]:
    cfg = load_pipeline()
    sig, meta, source_slug = inputs[row.id]
    tri = triage_one(sig, catalog, cfg.triage.max_chars, complete)
    out: dict[str, Any] = {
        "id": row.id,
        "triage_relevant": tri.relevant,
        "triage_reason": tri.reason,
    }
    if not tri.relevant:
        return out
    e = enrich_one(sig, meta, catalog, schemas, cfg.enrich, complete)
    r = e.result
    roles = link_roles([(ref.slug, ref.role) for ref in r.entities], source_slug)
    v = verify_one(
        sig,
        r.summary,
        r.why_jfrog_cares,
        r.evidence_quote,
        cfg.verify.max_chars,
        complete,
        takeaway=r.takeaway,
        display_title=getattr(r, "display_title", None),
    )
    out |= {
        "category": str(r.category),
        "impact": r.impact,
        "primary_entities": sorted(s for s, p in roles.items() if p),
        "quote_ok": e.quote_ok,
        "quote_repaired": e.repair is not None,
        "quote_attempts": e.attempts,
        "summary": r.summary,
        "why": r.why_jfrog_cares,
        "quote": r.evidence_quote,
        "verdict": v.verdict,
        "unsupported_claims": list(v.unsupported_claims),
    }
    return out


def _verify_alt(item: dict[str, Any], inputs: dict, level: str) -> dict[str, Any]:
    sig = inputs[item["id"]][0]
    v = verify_one(
        sig,
        item["summary"],
        item["why"],
        item["quote"],
        load_pipeline().verify.max_chars,
        complete,
        thinking=level,
    )
    return {"verdict": v.verdict, "unsupported_claims": list(v.unsupported_claims)}


def run_pass(factory: sessionmaker[Session]) -> dict[str, Any]:
    rows = [r for r in read_labels() if r.is_labeled()]
    with factory() as s:
        catalog = Catalog.load(s)
        inputs = _inputs(s, [r.id for r in rows])
    schemas = enrich_schemas(catalog.entity_slugs, catalog.capability_slugs)

    with eval_run(factory, "eval_pipeline") as main_counts:
        results = parallel_map(lambda r: _process(r, inputs, catalog, schemas), rows)
        failures = [
            {"id": r.id, "error": f"{type(x).__name__}: {x}"[:300]}
            for r, x in zip(rows, results, strict=True)
            if isinstance(x, Exception)
        ]
        items = [x for x in results if not isinstance(x, Exception)]
        main_counts.update(items=len(items), failed=len(failures))

    enriched = [i for i in items if "verdict" in i]
    alt = thinking_levels()[1]
    with eval_run(factory, f"eval_verify_{alt}") as off_counts:
        offs = parallel_map(lambda i: _verify_alt(i, inputs, alt), enriched)
        for item, off in zip(enriched, offs, strict=True):
            item["verify_alt"] = (
                {"error": f"{type(off).__name__}: {off}"[:300]}
                if isinstance(off, Exception)
                else off
            )
        off_counts.update(items=len(enriched))

    return {
        "rows": rows,
        "items": {i["id"]: i for i in items},
        "failures": failures,
        "run_ids": {"pipeline": main_counts["run_id"], "verify_alt": off_counts["run_id"]},
    }


# ---------- call statistics ----------


def call_stats(s: Session, run_id: int) -> list[dict[str, Any]]:
    """Per (model, operation): calls, cache hits, and the tokens / cost / latency of the original
    (uncached) call behind each one, so cached re-runs still report what the work costs."""
    calls = s.scalars(select(LLMCall).where(LLMCall.run_id == run_id)).all()
    keys = {c.cache_key for c in calls if c.cache_hit and c.cache_key}
    originals: dict[str, LLMCall] = {}
    if keys:
        for c in s.scalars(
            select(LLMCall)
            .where(
                LLMCall.cache_key.in_(keys), LLMCall.cache_hit.is_(False), LLMCall.success.is_(True)
            )
            .order_by(LLMCall.id)
        ):
            originals.setdefault(c.cache_key, c)
    agg: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "calls": 0,
            "cache_hits": 0,
            "paid_now_usd": Decimal(0),
            "work_usd": Decimal(0),
            "tokens_in": 0,
            "tokens_out": 0,
            "tokens_reasoning": 0,
            "latency_ms": [],
            "failed": 0,
        }
    )
    for c in calls:
        a = agg[(c.model, c.operation)]
        a["calls"] += 1
        if not c.success:
            a["failed"] += 1
        src = originals.get(c.cache_key, c) if c.cache_hit else c
        a["cache_hits"] += bool(c.cache_hit)
        a["paid_now_usd"] += c.cost_usd or 0
        a["work_usd"] += src.cost_usd or 0
        a["tokens_in"] += src.tokens_in or 0
        a["tokens_out"] += src.tokens_out or 0
        a["tokens_reasoning"] += src.tokens_reasoning or 0
        if src.latency_ms is not None and not (c.cache_hit and src is c):
            a["latency_ms"].append(src.latency_ms)
    out = []
    for (model, op), a in sorted(agg.items()):
        lat = sorted(a.pop("latency_ms"))
        out.append(
            {
                "model": model,
                "operation": op,
                **a,
                "paid_now_usd": float(a["paid_now_usd"]),
                "work_usd": float(a["work_usd"]),
                "median_latency_ms": lat[len(lat) // 2] if lat else None,
            }
        )
    return out


# ---------- comparison with the silver labels ----------


def compare(factory: sessionmaker[Session], passed: dict[str, Any]) -> dict[str, Any]:
    rows: list[LabelRow] = passed["rows"]
    items: dict[int, dict] = passed["items"]
    with factory() as s:
        parents = dict(s.execute(select(Entity.slug, Entity.parent_id)).all())
        slug_by_id = dict(s.execute(select(Entity.id, Entity.slug)).all())
        stats = {k: call_stats(s, v) for k, v in passed["run_ids"].items()}
    family = {slug: slug_by_id.get(pid, slug) for slug, pid in parents.items() if pid}

    scored = [(r, items[r.id]) for r in rows if r.id in items]
    triage = metrics.binary(
        [i["triage_relevant"] for _, i in scored], [r.relevant == "y" for r, _ in scored]
    )
    disagreements = [
        {
            "id": r.id,
            "title": r.title,
            "source": r.source,
            "in_scope": r.in_scope,
            "pipeline": "relevant" if i["triage_relevant"] else "irrelevant",
            "silver": "relevant" if r.relevant == "y" else "irrelevant",
            "pipeline_reason": i["triage_reason"],
            "silver_reason": r.label_reason,
        }
        for r, i in scored
        if i["triage_relevant"] != (r.relevant == "y")
    ]
    both = [(r, i) for r, i in scored if r.relevant == "y" and "category" in i]
    cat = metrics.category([(i["category"], r.category) for r, i in both])
    cat["mismatches"] = [
        {"id": r.id, "title": r.title, "silver": r.category, "pipeline": i["category"]}
        for r, i in both
        if i["category"] != r.category
    ]
    imp = metrics.impact([(i["impact"], int(r.impact)) for r, i in both])
    ent = metrics.entity([(r.id, i["primary_entities"], r.primary_entity) for r, i in both], family)
    titles = {r.id: r.title for r in rows}
    for miss in ent["misses"]:
        miss["title"] = titles[miss["id"]]

    enriched = [i | {"title": r.title} for r, i in scored if "verdict" in i]
    quotes = {
        "n": len(enriched),
        "verified": metrics.ratio(sum(i["quote_ok"] for i in enriched), len(enriched)),
        "repaired": metrics.ratio(sum(i["quote_repaired"] for i in enriched), len(enriched)),
        "retried": metrics.ratio(sum(i["quote_attempts"] > 1 for i in enriched), len(enriched)),
        "unverified_ids": [i["id"] for i in enriched if not i["quote_ok"]],
    }
    low = metrics.verifier(enriched)
    with_off = [i for i in enriched if "verdict" in i.get("verify_alt", {})]
    off = metrics.verifier([i | i["verify_alt"] for i in with_off])
    thinking = {
        "configured": thinking_levels()[0],
        "alternative": thinking_levels()[1],
        "matrix": metrics.agreement_matrix(
            [i["verdict"] for i in with_off], [i["verify_alt"]["verdict"] for i in with_off]
        ),
        "disagreements": [
            {
                "id": i["id"],
                "title": i["title"],
                "configured": i["verdict"],
                "alternative": i["verify_alt"]["verdict"],
            }
            for i in with_off
            if i["verdict"] != i["verify_alt"]["verdict"]
        ],
        "alternative_failures": [i["id"] for i in enriched if "error" in i.get("verify_alt", {})],
        "configured_verifier": low,
        "alternative_verifier": off,
    }

    dedup = None
    try:
        pairs = [p for p in read_pairs() if p.is_labeled()]
    except FileNotFoundError:
        pairs = []
    if pairs:
        dedup = metrics.dedup_sweep(
            [(p.similarity, p.same_story == "y", p.band, p.band_population) for p in pairs]
        )
        dedup["bands"] = _band_table(pairs)
        dedup["current"] = load_pipeline().dedup.cross_source_threshold

    labels = [r for r in rows if r.is_labeled()]
    return {
        "date": datetime.now(UTC).date().isoformat(),
        "prompt_versions": prompt_versions(),
        "label_source": sorted({r.label_source for r in labels}),
        "set": {
            "items": len(labels),
            "in_scope": sum(r.in_scope == "y" for r in labels),
            "silver_relevant": sum(r.relevant == "y" for r in labels),
            "pairs": len(pairs),
            "same_story_pairs": sum(p.same_story == "y" for p in pairs),
        },
        "run_ids": passed["run_ids"],
        "failures": passed["failures"],
        "triage": triage | {"disagreements": disagreements},
        "category": cat,
        "impact": imp,
        "entity": ent,
        "quotes": quotes,
        "verifier": low,
        "thinking": thinking,
        "dedup": dedup,
        "calls": stats,
        "items": [_public(r, items.get(r.id)) for r in rows],
    }


def _band_table(pairs) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in pairs:
        b = out.setdefault(
            p.band, {"band": p.band, "population": p.band_population, "sampled": 0, "same_story": 0}
        )
        b["sampled"] += 1
        b["same_story"] += p.same_story == "y"
    return [out[k] for k in sorted(out)]


def _public(r: LabelRow, i: dict | None) -> dict[str, Any]:
    """Per-item record for the JSON: labels and pipeline outputs, no source text or quotes."""
    base = {
        "id": r.id,
        "title": r.title,
        "in_scope": r.in_scope,
        "stratum": r.stratum,
        "silver": {
            "relevant": r.relevant,
            "category": r.category,
            "impact": r.impact,
            "primary_entity": r.primary_entity,
        },
    }
    if i is None:
        return base | {"pipeline": None}
    keep = (
        "triage_relevant",
        "category",
        "impact",
        "primary_entities",
        "quote_ok",
        "quote_repaired",
        "verdict",
        "unsupported_claims",
    )
    p = {k: i[k] for k in keep if k in i}
    if "verify_alt" in i:
        p["verdict_alternative_thinking"] = i["verify_alt"].get("verdict")
    return base | {"pipeline": p}
