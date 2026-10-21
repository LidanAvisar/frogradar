"""Hybrid retrieval (D65): pgvector + full text, merged with reciprocal rank fusion.

Signals are ranked in SQL (cosine on the stored embedding, ts_rank on search_tsv); battlecard items
and radar profiles (a few dozen short texts) are embedded through the gateway cache and ranked in
Python / one SQL statement. Filters come from app.rag.filters and are applied here as SQL / code
conditions. Full text uses OR of the question's lexemes (websearch syntax would AND every word of a
natural question and match almost nothing).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import bindparam, cast, func, select, text
from sqlalchemy.dialects.postgresql import ARRAY, TSQUERY
from sqlalchemy.orm import Session
from sqlalchemy.types import Text

from app.core.catalog import AskSettings
from app.db.models import Signal, signal_entities
from app.pipeline.context import EmbedFn
from app.pipeline.scope import event_time, usable
from app.rag.documents import Doc, battlecard_docs, radar_docs, signal_docs
from app.rag.filters import Filters


@dataclass(frozen=True)
class Hit:
    doc: Doc
    score: float  # RRF score
    vector_rank: int | None
    text_rank: int | None
    cosine: float | None
    category_rank: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.doc.id,
            "type": self.doc.type,
            "score": round(self.score, 6),
            "vector_rank": self.vector_rank,
            "text_rank": self.text_rank,
            "category_rank": self.category_rank,
            "cosine": round(self.cosine, 4) if self.cosine is not None else None,
        }


@dataclass
class Retrieval:
    hits: list[Hit] = field(default_factory=list)
    candidates: int = 0  # distinct docs in either list before the cut

    @property
    def docs(self) -> list[Doc]:
        return [h.doc for h in self.hits]


def rrf(lists: Sequence[Sequence[str]], k: int) -> list[tuple[str, float, list[int | None]]]:
    """Reciprocal rank fusion: score = sum over lists of 1 / (k + rank), rank from 1. Returns
    (id, score, rank per list) best first; ties keep the order of first appearance."""
    scores: dict[str, float] = {}
    ranks: dict[str, list[int | None]] = {}
    order: list[str] = []
    for li, ids in enumerate(lists):
        for rank, doc_id in enumerate(ids, start=1):
            if doc_id not in ranks:
                ranks[doc_id] = [None] * len(lists)
                order.append(doc_id)
            if ranks[doc_id][li] is None:  # a duplicate within one list counts once
                ranks[doc_id][li] = rank
                scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    pos = {d: i for i, d in enumerate(order)}
    out = sorted(order, key=lambda d: (-scores[d], pos[d]))
    return [(d, scores[d], ranks[d]) for d in out]


def _or_query(question: str) -> Any:
    """tsquery matching ANY lexeme of the question (stop words dropped by the parser)."""
    # Cast, not to_tsquery(): the lexemes are already stemmed and must not be stemmed again.
    plain = func.plainto_tsquery("english", question)
    return cast(func.replace(cast(plain, Text), "&", "|"), TSQUERY)


def _signal_conditions(f: Filters) -> list[Any]:
    conds = [usable()]
    if f.entity_ids:
        conds.append(
            Signal.id.in_(
                select(signal_entities.c.signal_id).where(
                    signal_entities.c.entity_id.in_(f.entity_ids)
                )
            )
        )
    if f.since:
        conds.append(event_time() >= f.since)
    if f.until:
        conds.append(event_time() < f.until)
    return conds


def _cut(
    fused: list[tuple[str, float, list[int | None]]], k: int, max_per_type: dict[str, int]
) -> list[tuple[str, float, list[int | None]]]:
    """Top k, at most max_per_type[t] documents of type t (bc: battlecard, radar: radar)."""
    out, per = [], dict.fromkeys(max_per_type, 0)
    for item in fused:
        t = (
            "battlecard"
            if item[0].startswith("bc:")
            else "radar"
            if item[0].startswith("radar:")
            else None
        )
        if t in per:
            if per[t] >= max_per_type[t]:
                continue
            per[t] += 1
        out.append(item)
        if len(out) == k:
            break
    return out


def _keep_other(doc: Doc, f: Filters) -> bool:
    """Filters for battlecard / radar documents."""
    if doc.type == "battlecard":
        # Undated analysis: left out of time-bounded questions; kept for the named company only.
        if f.since or f.until:
            return False
        return not f.entity_ids or bool(doc.entity_ids & set(f.entity_ids))
    if doc.type == "radar":
        if f.entity_ids:  # radar candidates are players we don't track
            return False
        if f.since and (doc.date is None or doc.date < f.since):
            return False
        return not (f.until and doc.date and doc.date >= f.until)
    return True


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def retrieve(
    session: Session, question: str, f: Filters, *, embed: EmbedFn, cfg: AskSettings
) -> Retrieval:
    qvec = embed([question]).vectors[0]
    conds = _signal_conditions(f)
    n = cfg.candidates

    # Signals: vector list (stored embeddings) and full-text list, in SQL.
    distance = Signal.embedding.cosine_distance(qvec)
    vec_rows = session.execute(
        select(Signal.id, (1 - distance).label("sim"))
        .where(*conds, Signal.embedding.is_not(None))
        .order_by(distance, Signal.id)
        .limit(n)
    ).all()
    tsq = _or_query(question)
    txt_rows = session.execute(
        select(Signal.id, func.ts_rank(Signal.search_tsv, tsq).label("rank"))
        .where(*conds, Signal.search_tsv.op("@@")(tsq))
        .order_by(text("rank DESC"), Signal.id)
        .limit(n)
    ).all()

    # Signals of a category the question names (newest high impact first): a third ranked list.
    cat_rows = []
    if f.categories:
        cat_rows = session.execute(
            select(Signal.id)
            .where(*conds, Signal.category.in_(f.categories))
            .order_by(Signal.impact.desc().nulls_last(), event_time().desc(), Signal.id.desc())
            .limit(n)
        ).all()

    # Battlecard items and radar profiles, filtered in code.
    others = [d for d in (*battlecard_docs(session), *radar_docs(session)) if _keep_other(d, f)]
    other_vec: list[tuple[str, float]] = []
    other_txt: list[tuple[str, float]] = []
    if others:
        vectors = embed([d.text for d in others]).vectors
        other_vec = [(d.id, _cosine(qvec, v)) for d, v in zip(others, vectors, strict=True)]
        texts = [d.text for d in others]
        ranked = session.execute(
            select(text("x.i"), text("ts_rank(to_tsvector('english', x.t), q.q) AS r"))
            .select_from(
                text(
                    "unnest(:texts) WITH ORDINALITY AS x(t, i), "
                    "(SELECT replace(plainto_tsquery('english', :q)::text, '&', '|')::tsquery "
                    "AS q) q"
                ).bindparams(
                    bindparam("texts", value=texts, type_=ARRAY(Text)),
                    bindparam("q", value=question),
                )
            )
            .where(text("to_tsvector('english', x.t) @@ q.q"))
        ).all()
        other_txt = [(others[int(i) - 1].id, float(r)) for i, r in ranked]

    vec = [(f"sig:{r.id}", float(r.sim)) for r in vec_rows] + other_vec
    vec = [v for v in vec if v[1] >= cfg.min_cosine]
    vec.sort(key=lambda v: -v[1])
    txt = [(f"sig:{r.id}", float(r.rank)) for r in txt_rows] + other_txt
    txt.sort(key=lambda v: -v[1])
    vec, txt = vec[:n], txt[:n]
    cat = [f"sig:{r.id}" for r in cat_rows]
    # Battlecard items of a section the question names, in vector order.
    order = {d: i for i, (d, _) in enumerate(sorted(other_vec, key=lambda v: -v[1]))}
    sec = sorted(
        (d.id for d in others if d.type == "battlecard" and d.id.split(":")[2] in f.sections),
        key=lambda d: order.get(d, len(order)),
    )
    cat += sec  # one "named in the question" list: categories for signals, sections for cards
    fused = rrf([[d for d, _ in vec], [d for d, _ in txt], cat], cfg.rrf_k)

    top = _cut(fused, min(cfg.k, cfg.max_docs), cfg.max_per_type)
    sig_ids = [int(d[4:]) for d, _, _ in top if d.startswith("sig:")]
    by_id: dict[str, Doc] = {d.id: d for d in others}
    by_id |= {d.id: d for d in signal_docs(session, sig_ids).values()}
    all_cos = {f"sig:{r.id}": float(r.sim) for r in vec_rows} | dict(other_vec)
    hits = [
        Hit(by_id[d], score, ranks[0], ranks[1], all_cos.get(d), ranks[2])
        for d, score, ranks in top
        if d in by_id
    ]
    return Retrieval(hits=hits, candidates=len(fused))


__all__ = ["Hit", "Retrieval", "retrieve", "rrf"]
