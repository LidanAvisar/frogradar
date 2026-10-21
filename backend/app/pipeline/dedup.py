"""Semantic dedup: group signals that report the same news into clusters.

Runs after the exact content_hash dedup done at collection. For each in-scope signal that has an
embedding (current model) but no cluster yet, oldest first:

1. Find its nearest neighbours (HNSW, cosine) among already-clustered in-scope signals published
   within ±window_days.
2. A neighbour's cluster is a candidate. The signal may join it only if its similarity to the
   cluster's *canonical* signal reaches the threshold (cross_source_threshold, or
   same_source_threshold when both come from the same source). Comparing with the canonical, not
   with any member, prevents chaining (A~B, B~C, but A and C unrelated).
3. It must also reach same_source_threshold with every member from its own source, so templated
   entries of one changelog never end up together through a third article.
4. It joins the best candidate (highest similarity to the canonical); otherwise it starts its own
   cluster (dedup_cluster_id = its own id).

The canonical signal is the member with the highest source trust_score, then the earliest
published_at, then the lowest id. Duplicates are never deleted: they point at the canonical, and
meta.dedup records the cluster (the canonical lists members and sources; "reported by N sources").
Already-clustered signals are never revisited, so re-running changes nothing.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select, text, update
from sqlalchemy.orm import Session

from app.db.models import Signal, Source
from app.pipeline.context import RunContext
from app.pipeline.scope import in_scope, not_discovery

logger = logging.getLogger(__name__)


def signal_time() -> ColumnElement[datetime]:
    """When a signal happened: published_at, or fetched_at when the source gave no date."""
    return func.coalesce(Signal.published_at, Signal.fetched_at)


@dataclass(frozen=True)
class Member:
    id: int
    source_id: int | None
    trust: float
    when: datetime

    def canonical_key(self) -> tuple[float, datetime, int]:
        return (-self.trust, self.when, self.id)


def same_source(a: int | None, b: int | None) -> bool:
    # Signals without a source (research findings) are never "the same source".
    return a is not None and a == b


def set_hnsw_search(session: Session, k: int) -> None:
    """Filtered HNSW queries: scan further so WHERE filters don't starve the result (pgvector)."""
    session.execute(text(f"SET LOCAL hnsw.ef_search = {max(100, 4 * k)}"))
    session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))


def _member_query() -> Select[Any]:
    return (
        select(
            Signal.id,
            Signal.source_id,
            func.coalesce(Source.trust_score, 0.0).label("trust"),
            signal_time().label("when"),
        )
        .select_from(Signal)
        .outerjoin(Source, Source.id == Signal.source_id)
    )


def _member(row: Any) -> Member:
    return Member(row.id, row.source_id, float(row.trust), row.when)


def dedup_signals(session: Session, ctx: RunContext) -> dict[str, Any]:
    cfg = ctx.config.dedup
    window = timedelta(days=cfg.window_days)
    set_hnsw_search(session, cfg.neighbours)
    pending = session.execute(
        _member_query()
        .add_columns(Signal.embedding)
        .where(
            in_scope(),
            not_discovery(),  # discovery items never join news clusters (D57)
            Signal.embedding.is_not(None),
            Signal.embedding_model == ctx.embedding_model,
            Signal.dedup_cluster_id.is_(None),
        )
        .order_by(signal_time(), Signal.id)
    ).all()

    counts = {"pending": len(pending), "new_clusters": 0, "joined": 0, "canonical_changed": 0}
    for row in pending:
        sig = _member(row)
        best = _best_cluster(session, ctx, sig, row.embedding, window)
        if best is None:
            _assign(session, [sig.id], sig.id)
            counts["new_clusters"] += 1
            continue
        canonical = best
        counts["joined"] += 1
        if sig.canonical_key() < canonical.canonical_key():
            # The newcomer is more trusted (or older): it becomes the canonical signal.
            session.execute(
                update(Signal)
                .where(Signal.dedup_cluster_id == canonical.id)
                .values(dedup_cluster_id=sig.id)
            )
            _assign(session, [sig.id], sig.id)
            counts["canonical_changed"] += 1
        else:
            _assign(session, [sig.id], canonical.id)
    session.flush()

    counts["meta_refreshed"] = refresh_cluster_meta(session, ctx)
    counts.update(cluster_stats(session, ctx))
    logger.info("dedup: %s", counts)
    return counts


def _best_cluster(
    session: Session, ctx: RunContext, sig: Member, vector: Any, window: timedelta
) -> Member | None:
    cfg = ctx.config.dedup
    distance = Signal.embedding.cosine_distance(vector)
    neighbours = session.execute(
        select(Signal.dedup_cluster_id)
        .where(
            in_scope(),
            not_discovery(),
            Signal.id != sig.id,
            Signal.embedding_model == ctx.embedding_model,
            Signal.dedup_cluster_id.is_not(None),
            signal_time().between(sig.when - window, sig.when + window),
        )
        .order_by(distance)
        .limit(cfg.neighbours)
    ).scalars()
    cluster_ids = sorted(set(neighbours))
    if not cluster_ids:
        return None

    # Similarity to each candidate cluster's canonical signal (also within the window).
    canonicals = session.execute(
        _member_query()
        .add_columns((1 - distance).label("sim"))
        .where(
            Signal.id.in_(cluster_ids),
            signal_time().between(sig.when - window, sig.when + window),
        )
    ).all()
    best: tuple[float, Member] | None = None
    for c in canonicals:
        canonical = _member(c)
        threshold = (
            cfg.same_source_threshold
            if same_source(sig.source_id, canonical.source_id)
            else cfg.cross_source_threshold
        )
        if c.sim < threshold or not _same_source_members_ok(session, ctx, sig, canonical, vector):
            continue
        if best is None or (c.sim, -canonical.id) > (best[0], -best[1].id):
            best = (c.sim, canonical)
    return best[1] if best else None


def _same_source_members_ok(
    session: Session, ctx: RunContext, sig: Member, canonical: Member, vector: Any
) -> bool:
    """Every cluster member from the signal's own source must be a near-copy of it."""
    if sig.source_id is None:
        return True
    sims = session.scalars(
        select(1 - Signal.embedding.cosine_distance(vector)).where(
            Signal.dedup_cluster_id == canonical.id, Signal.source_id == sig.source_id
        )
    ).all()
    return all(s >= ctx.config.dedup.same_source_threshold for s in sims)


def _assign(session: Session, ids: list[int], canonical_id: int) -> None:
    session.execute(update(Signal).where(Signal.id.in_(ids)).values(dedup_cluster_id=canonical_id))


# ---------- meta.dedup ----------


def refresh_cluster_meta(session: Session, ctx: RunContext) -> int:
    """Rewrite meta.dedup for every cluster whose stored summary is out of date. Returns count.

    Canonical: {canonical_id, members, source_ids, source_count}. Duplicate: {canonical_id,
    similarity} (to the canonical). Self-healing: also fixes clusters changed by a re-embed.
    """
    rows = session.execute(
        select(Signal.id, Signal.dedup_cluster_id, Signal.source_id, Signal.meta).where(
            in_scope(), Signal.dedup_cluster_id.is_not(None)
        )
    ).all()
    clusters: dict[int, list[Any]] = defaultdict(list)
    for r in rows:
        clusters[r.dedup_cluster_id].append(r)

    refreshed = 0
    for canonical_id, members in clusters.items():
        ids = sorted(m.id for m in members)
        head = next((m for m in members if m.id == canonical_id), None)
        if head is None:
            continue  # canonical out of scope; the cluster is reclustered when it matters
        stored = (head.meta or {}).get("dedup") or {}
        if stored.get("members") == ids and all(
            ((m.meta or {}).get("dedup") or {}).get("canonical_id") == canonical_id for m in members
        ):
            continue
        source_ids = sorted({m.source_id for m in members if m.source_id is not None})
        sims = dict(
            session.execute(
                select(
                    Signal.id,
                    1
                    - Signal.embedding.cosine_distance(
                        select(Signal.embedding).where(Signal.id == canonical_id).scalar_subquery()
                    ),
                ).where(Signal.id.in_(ids))
            ).all()
        )
        for m in members:
            if m.id == canonical_id:
                info = {
                    "canonical_id": canonical_id,
                    "members": ids,
                    "source_ids": source_ids,
                    "source_count": len(source_ids),
                }
            else:
                sim = sims.get(m.id)
                info = {
                    "canonical_id": canonical_id,
                    "similarity": None if sim is None else round(float(sim), 4),
                }
            session.execute(
                update(Signal)
                .where(Signal.id == m.id)
                .values(meta={**(m.meta or {}), "dedup": info})
            )
        refreshed += 1
    session.flush()
    return refreshed


def cluster_stats(session: Session, ctx: RunContext) -> dict[str, int]:
    sizes = session.execute(
        select(func.count())
        .where(in_scope(), Signal.dedup_cluster_id.is_not(None))
        .group_by(Signal.dedup_cluster_id)
    ).scalars()
    sizes = list(sizes)
    return {
        "clustered_signals": sum(sizes),
        "clusters": len(sizes),
        "multi_signal_clusters": sum(1 for s in sizes if s > 1),
        "duplicates": sum(s - 1 for s in sizes),
    }
