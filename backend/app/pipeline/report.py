"""Calibration report for dedup thresholds and entity linking (`make dedup-report`). No LLM calls.

- Top cross-source and same-source pairs by cosine similarity (within the dedup window), with
  whether dedup merged them.
- Distribution of each signal's nearest cross-source neighbour, in 0.05 buckets: where a
  threshold would cut.
- Entity link counts (primary / mention) and the signals with the most links (over-linking).
"""

import sys
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, TextIO

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.models import Entity, Signal, Source, signal_entities
from app.pipeline.context import RunContext
from app.pipeline.dedup import set_hnsw_search, signal_time
from app.pipeline.scope import in_scope

TOP_CROSS, TOP_SAME, TOP_LINKED = 20, 10, 10
CROSS_K, SAME_K = 10, 5
TITLE_CHARS = 60


@dataclass(frozen=True)
class Pair:
    a: int
    b: int
    sim: float
    same_source: bool


def _rows(session: Session, ctx: RunContext) -> list[Any]:
    return session.execute(
        select(
            Signal.id,
            Signal.source_id,
            Signal.title,
            Signal.embedding,
            Signal.dedup_cluster_id,
            signal_time().label("when"),
        )
        .where(
            in_scope(),
            Signal.embedding.is_not(None),
            Signal.embedding_model == ctx.embedding_model,
        )
        .order_by(Signal.id)
    ).all()


def _neighbours(
    session: Session, ctx: RunContext, row: Any, *, same: bool, k: int
) -> list[tuple[int, float]]:
    window = timedelta(days=ctx.config.dedup.window_days)
    distance = Signal.embedding.cosine_distance(row.embedding)
    source_filter = (
        Signal.source_id == row.source_id
        if same
        else Signal.source_id.is_distinct_from(row.source_id)
    )
    q = (
        select(Signal.id, (1 - distance).label("sim"))
        .where(
            in_scope(),
            Signal.id != row.id,
            Signal.embedding_model == ctx.embedding_model,
            signal_time().between(row.when - window, row.when + window),
            source_filter,
        )
        .order_by(distance)
        .limit(k)
    )
    # relaxed_order may return slightly out of order: sort again.
    return sorted(((i, float(s)) for i, s in session.execute(q).all()), key=lambda x: -x[1])


def collect_pairs(session: Session, ctx: RunContext) -> tuple[list[Pair], list[float], list[Any]]:
    """(unique pairs, nearest cross-source similarity per signal, signal rows)."""
    set_hnsw_search(session, CROSS_K)
    rows = _rows(session, ctx)
    pairs: dict[tuple[int, int], Pair] = {}
    nearest_cross: list[float] = []
    for row in rows:
        if row.source_id is None:
            continue
        cross = _neighbours(session, ctx, row, same=False, k=CROSS_K)
        same = _neighbours(session, ctx, row, same=True, k=SAME_K)
        if cross:
            nearest_cross.append(cross[0][1])
        for other, sim, is_same in [(i, s, False) for i, s in cross] + [
            (i, s, True) for i, s in same
        ]:
            key = (min(row.id, other), max(row.id, other))
            pairs.setdefault(key, Pair(*key, sim, is_same))
    return list(pairs.values()), nearest_cross, rows


def buckets(values: list[float], lo: float = 0.5, step: float = 0.05) -> list[tuple[str, int]]:
    """Counts per [x, x+step) bucket from lo to 1.0, plus one bucket below lo."""
    edges = [round(lo + i * step, 2) for i in range(round((1 - lo) / step))]
    out = [(f"< {lo:.2f}", sum(v < lo for v in values))]
    for e in edges:
        hi = round(e + step, 2)
        n = sum(e <= v < hi or (hi >= 1 and v >= e) for v in values)
        out.append((f"{e:.2f}–{hi:.2f}", n))
    return out


def _short(title: str) -> str:
    return title if len(title) <= TITLE_CHARS else title[: TITLE_CHARS - 1] + "…"


def print_report(session: Session, ctx: RunContext, out: TextIO = sys.stdout) -> None:
    cfg = ctx.config.dedup
    sources = dict(session.execute(select(Source.id, Source.name)).all())
    pairs, nearest_cross, rows = collect_pairs(session, ctx)
    by_id = {r.id: r for r in rows}

    def p(line: str = "") -> None:
        print(line, file=out)

    p(f"Dedup calibration – {len(rows)} embedded in-scope signals ({ctx.embedding_model})")
    p(
        f"window ±{cfg.window_days}d · cross-source threshold {cfg.cross_source_threshold} · "
        f"same-source threshold {cfg.same_source_threshold} · similarity to the canonical decides"
    )

    def merged(pair: Pair) -> str:
        a, b = by_id[pair.a].dedup_cluster_id, by_id[pair.b].dedup_cluster_id
        return "yes" if a is not None and a == b else "no"

    def side(i: int) -> str:
        r = by_id[i]
        src = (sources.get(r.source_id) or "-")[:16]
        return f"#{i:<5} {src:<16} {_short(r.title)}"

    for label, is_same, top in (
        ("cross-source", False, TOP_CROSS),
        ("same-source", True, TOP_SAME),
    ):
        chosen = sorted((x for x in pairs if x.same_source is is_same), key=lambda x: -x.sim)
        p(f"\nTop {top} {label} pairs")
        p(f"{'sim':>6}  {'merged':<6}  signal A / signal B")
        for pair in chosen[:top]:
            p(f"{pair.sim:6.3f}  {merged(pair):<6}  {side(pair.a)}")
            p(f"{'':6}  {'':6}  {side(pair.b)}")
        if not chosen:
            p("  (none)")

    p(f"\nNearest cross-source neighbour per signal ({len(nearest_cross)} signals)")
    for name, n in buckets(nearest_cross):
        lo, hi = (float(x) for x in name.split("–")) if "–" in name else (0.0, 0.5)
        thr = round(cfg.cross_source_threshold, 4)
        mark = "  ← cross-source threshold" if lo <= thr < hi or (hi >= 1 and thr >= lo) else ""
        p(f"  {name:>11}  {n:3d}  {'█' * n}{mark}")

    sizes: dict[int, int] = {}
    for r in rows:
        if r.dedup_cluster_id is not None:
            sizes[r.dedup_cluster_id] = sizes.get(r.dedup_cluster_id, 0) + 1
    multi = {k: v for k, v in sizes.items() if v > 1}
    p(
        f"\nClusters: {len(sizes)} for {sum(sizes.values())} clustered signals; "
        f"{len(multi)} with duplicates ({sum(v - 1 for v in multi.values())} duplicates)"
    )
    for canonical_id, n in sorted(multi.items(), key=lambda kv: -kv[1]):
        members = [r for r in rows if r.dedup_cluster_id == canonical_id]
        n_sources = len({r.source_id for r in members})
        title = _short(by_id[canonical_id].title)
        p(f"  #{canonical_id} ({n} signals, {n_sources} sources): {title}")

    _print_links(session, p, sources)


def _print_links(session: Session, p: Any, sources: dict[int, str]) -> None:
    primary = func.count().filter(signal_entities.c.is_primary)
    mention = func.count().filter(~signal_entities.c.is_primary)
    per_entity = session.execute(
        select(Entity.slug, Entity.type, primary, mention)
        .select_from(signal_entities)
        .join(Entity, Entity.id == signal_entities.c.entity_id)
        .join(Signal, Signal.id == signal_entities.c.signal_id)
        .where(in_scope())
        .group_by(Entity.slug, Entity.type)
        .order_by(func.count().desc(), Entity.slug)
    ).all()
    p("\nEntity links (in-scope signals)")
    p(f"  {'entity':<30} {'type':<8} {'primary':>7} {'mention':>7}")
    for slug, type_, n_primary, n_mention in per_entity:
        p(f"  {slug:<30} {type_:<8} {n_primary:>7} {n_mention:>7}")
    if not per_entity:
        p("  (none)")

    n_links = func.count().label("n")
    top = session.execute(
        select(
            Signal.id,
            Signal.title,
            Signal.source_id,
            n_links,
            func.array_agg(
                case((signal_entities.c.is_primary, Entity.slug + "*"), else_=Entity.slug)
            ),
        )
        .select_from(signal_entities)
        .join(Signal, Signal.id == signal_entities.c.signal_id)
        .join(Entity, Entity.id == signal_entities.c.entity_id)
        .where(in_scope())
        .group_by(Signal.id)
        .order_by(n_links.desc(), Signal.id)
        .limit(TOP_LINKED)
    ).all()
    p(f"\nTop {TOP_LINKED} signals by entity links (* = primary)")
    for sid, title, source_id, n, slugs in top:
        src = (sources.get(source_id) or "-")[:16]
        p(f"  {n:2d}  #{sid:<5} {src:<16} {_short(title)}")
        p(f"      {', '.join(sorted(slugs))}")
