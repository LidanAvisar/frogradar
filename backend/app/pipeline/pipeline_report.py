"""Quality report for the enrichment pipeline (`make pipeline-report [RUN=<id>]`). No LLM calls.

Triage relevant vs not (every irrelevant signal with its reason, to spot false negatives),
category / impact / verification / confidence distributions, evidence-quote failures, cost per
model for the run, and a sample of enriched signals in full for a quality read.
"""

import argparse
import sys
import textwrap
from collections import Counter
from typing import Any, TextIO

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Entity, LLMCall, PipelineRun, Signal, Source, signal_entities
from app.pipeline.llm_steps import canonical_or_unclustered
from app.pipeline.scope import in_scope

SAMPLE = 8
RELEVANT_EXAMPLES = 5
WIDTH = 100


def _wrap(label: str, text: str | None, out: TextIO) -> None:
    body = textwrap.fill(
        text or "-", WIDTH, initial_indent=f"    {label:<16}", subsequent_indent=" " * 20
    )
    print(body, file=out)


def _bar(n: int, total: int) -> str:
    return "█" * round(30 * n / total) if total else ""


def _hist(title: str, counter: Counter[Any], out: TextIO, order: list[Any] | None = None) -> None:
    total = sum(counter.values())
    print(f"\n{title} (n={total})", file=out)
    for key in order or sorted(counter, key=lambda k: (-counter[k], str(k))):
        n = counter.get(key, 0)
        print(f"  {str(key):<22} {n:4d}  {_bar(n, total)}", file=out)


def latest_run(session: Session) -> PipelineRun | None:
    return session.scalars(
        select(PipelineRun)
        .where(PipelineRun.counts.has_key("graph"))
        .order_by(PipelineRun.id.desc())
        .limit(1)
    ).first()


def print_report(session: Session, run_id: int | None = None, out: TextIO = sys.stdout) -> None:
    run = session.get(PipelineRun, run_id) if run_id else latest_run(session)
    sources = dict(session.execute(select(Source.id, Source.name)).all())
    rows = session.scalars(
        select(Signal).where(in_scope(), canonical_or_unclustered()).order_by(Signal.id)
    ).all()

    def p(line: str = "") -> None:
        print(line, file=out)

    if run:
        p(
            f"Pipeline run {run.id}: {run.status} · cost ${run.cost_usd:.4f} · "
            f"started {run.started_at:%Y-%m-%d %H:%M}"
        )
        for node in ("collect", "embed", "dedup", "link", "triage", "enrich", "verify", "score"):
            c = run.counts.get(node)
            if isinstance(c, dict):
                shown = {k: v for k, v in c.items() if k not in ("errors", "sources")}
                p(f"  {node:<8} {shown}")
    p(f"\nIn-scope canonical signals: {len(rows)}")

    # ---------- triage ----------
    relevant = [s for s in rows if s.relevant is True]
    irrelevant = [s for s in rows if s.relevant is False]
    untriaged = [s for s in rows if s.relevant is None]
    p(
        f"\nTriage: {len(relevant)} relevant · {len(irrelevant)} irrelevant · "
        f"{len(untriaged)} not triaged"
    )
    seg = Counter(x for s in relevant for x in (s.meta.get("triage") or {}).get("segments", []))
    p("  segments of relevant signals: " + ", ".join(f"{k} {v}" for k, v in seg.most_common()))
    p(f"\n  {RELEVANT_EXAMPLES} relevant examples:")
    for s in relevant[:RELEVANT_EXAMPLES]:
        reason = (s.meta.get("triage") or {}).get("reason", "")
        p(f"  + #{s.id:<5} {(sources.get(s.source_id) or '-')[:16]:<16} {s.title[:70]}")
        p(f"           {reason}")
    p(f"\n  ALL irrelevant signals ({len(irrelevant)}) – check for false negatives:")
    for s in irrelevant:
        reason = (s.meta.get("triage") or {}).get("reason", "")
        p(f"  - #{s.id:<5} {(sources.get(s.source_id) or '-')[:16]:<16} {s.title[:70]}")
        p(f"           {reason}")

    # ---------- distributions ----------
    enriched = [s for s in relevant if s.enriched_at is not None]
    _hist("Category", Counter(str(s.category) for s in enriched), out)
    _hist("Impact", Counter(s.impact for s in enriched), out, order=[5, 4, 3, 2, 1])
    _hist(
        "Verification",
        Counter(str(s.verification) for s in enriched),
        out,
        order=["supported", "partial", "unsupported", "pending"],
    )
    scored = [s for s in enriched if s.confidence is not None]
    _hist(
        "Confidence band",
        Counter((s.meta.get("score") or {}).get("band") for s in scored),
        out,
        order=["high", "medium", "low"],
    )
    buckets = Counter(
        f"{min(int(s.confidence * 10), 9) / 10:.1f}–{min(int(s.confidence * 10), 9) / 10 + 0.1:.1f}"
        for s in scored
    )
    _hist("Confidence", buckets, out, order=sorted(buckets, reverse=True))
    claims = sum(1 for s in enriched if s.is_claim)
    p(f"\nVendor claims (is_claim): {claims} of {len(enriched)}")

    # ---------- evidence quotes ----------
    failures = [s for s in enriched if s.evidence_quote_verified is False]
    retried = [s for s in enriched if (s.meta.get("enrich") or {}).get("quote_attempts", 1) > 1]
    p(
        f"\nEvidence quotes: {len(failures)} not verbatim after retry · "
        f"{len(retried)} needed a retry"
    )
    for s in failures:
        p(f"  ✗ #{s.id} {s.title[:70]}")
        p(f"      quote: {(s.evidence_quote or '')[:160]!r}")

    # ---------- entities ----------
    review = Counter()
    new_entities: Counter[str] = Counter()
    for s in enriched:
        r = s.meta.get("entity_review") or {}
        for k in ("confirmed", "added", "dropped"):
            review[k] += len(r.get(k, []))
        for e in (s.meta.get("enrich") or {}).get("new_entities", []):
            new_entities[e["name"]] += 1
    p(
        f"\nEntity review by the LLM: {review['confirmed']} rule links confirmed, "
        f"{review['added']} added, {review['dropped']} dropped"
    )
    if new_entities:
        p(
            "New (untracked) entities mentioned: "
            + ", ".join(f"{k} ({v})" for k, v in new_entities.most_common(15))
        )

    # ---------- cost ----------
    if run:
        _print_cost(session, run.id, p)

    # ---------- sample ----------
    p(f"\n{'=' * WIDTH}\n{SAMPLE} enriched signals in full (highest impact, spread across sources)")
    for s in _sample(scored or enriched):
        _print_signal(session, s, sources, out)


def _print_cost(session: Session, run_id: int, p: Any) -> None:
    rows = session.execute(
        select(
            LLMCall.task,
            LLMCall.model,
            LLMCall.operation,
            func.count(),
            func.sum(case((LLMCall.cache_hit, 1), else_=0)),
            func.sum(case((LLMCall.success, 0), else_=1)),
            func.sum(LLMCall.tokens_in),
            func.sum(LLMCall.tokens_out),
            func.sum(LLMCall.cost_usd),
        )
        .where(LLMCall.run_id == run_id)
        .group_by(LLMCall.task, LLMCall.model, LLMCall.operation)
        .order_by(LLMCall.task, LLMCall.operation)
    ).all()
    p(f"\nCost for run {run_id} (from llm_calls)")
    row = "  {:<32} {:<14} {:>5} {:>6} {:>6} {:>8} {:>8} {:>9}"
    p(row.format("model", "operation", "calls", "cached", "failed", "tok in", "tok out", "cost $"))
    total = 0.0
    per_model: Counter[str] = Counter()
    for _task, model, op, n, hits, failed, tin, tout, cost in rows:
        c = float(cost or 0)
        total += c
        per_model[model] += c
        p(row.format(model, op, n, hits or 0, failed or 0, tin or 0, tout or 0, f"{c:.5f}"))
    for model, c in per_model.most_common():
        p(f"  total {model:<26} ${c:.5f}")
    p(f"  TOTAL ${total:.5f}")


def _sample(signals: list[Signal]) -> list[Signal]:
    ranked = sorted(signals, key=lambda s: (-(s.impact or 0), -(s.confidence or 0), s.id))
    by_source: dict[int | None, list[Signal]] = {}
    for s in ranked:
        by_source.setdefault(s.source_id, []).append(s)
    picked: list[Signal] = []
    while len(picked) < SAMPLE and any(by_source.values()):
        for queue in by_source.values():
            if queue and len(picked) < SAMPLE:
                picked.append(queue.pop(0))
    return sorted(picked, key=lambda s: (-(s.impact or 0), s.id))


def _print_signal(session: Session, s: Signal, sources: dict[int, str], out: TextIO) -> None:
    links = session.execute(
        select(Entity.slug, signal_entities.c.is_primary)
        .join(Entity, Entity.id == signal_entities.c.entity_id)
        .where(signal_entities.c.signal_id == s.id)
        .order_by(signal_entities.c.is_primary.desc(), Entity.slug)
    ).all()
    score = s.meta.get("score") or {}
    enrich = s.meta.get("enrich") or {}
    label = "  [vendor claim]" if s.is_claim else ""
    print(
        f"\n#{s.id} · {sources.get(s.source_id) or '-'} · {s.published_at:%Y-%m-%d}{label}",
        file=out,
    )
    print(f"  {s.title}", file=out)
    _wrap("category", str(s.category), out)
    _wrap("impact", f"{s.impact} – {enrich.get('impact_rationale', '')}", out)
    _wrap("summary", s.summary, out)
    _wrap("why JFrog cares", s.why_jfrog_cares, out)
    verified = "verbatim ✓" if s.evidence_quote_verified else "NOT verbatim ✗"
    _wrap("evidence", f'"{s.evidence_quote}" ({verified})', out)
    _wrap("verification", f"{s.verification} – {s.verification_notes}", out)
    _wrap(
        "confidence",
        f"{s.confidence} ({score.get('band')}) components={score.get('components')}"
        + (f" caps={score.get('caps_applied')}" if score.get("caps_applied") else ""),
        out,
    )
    _wrap("entities", ", ".join(f"{slug}{'*' if p else ''}" for slug, p in links), out)
    _wrap("capabilities", ", ".join(enrich.get("capabilities", [])), out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.pipeline.pipeline_report")
    parser.add_argument("--run", type=int, help="pipeline run id (default: latest graph run)")
    args = parser.parse_args(argv)
    from app.db.session import get_engine

    with sessionmaker(bind=get_engine())() as s:
        print_report(s, args.run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
