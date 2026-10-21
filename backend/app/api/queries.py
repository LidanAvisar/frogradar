"""Shared selects and Signal → SignalOut mapping for the read API.

"Visible" (what any screen may show as an enriched signal): in pipeline scope (active source or a
mission finding), the canonical member of its dedup cluster, and enriched. Rows whose text changed
after enrichment have enriched_at reset and keep stale summaries: hidden until re-enriched.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas import (
    ConfidenceOut,
    EntityRef,
    EvidenceOut,
    PageDiffOut,
    SignalOut,
    SourceOut,
)
from app.core.catalog import load_pipeline
from app.db.enums import FetchMethod, Verification
from app.db.models import Entity, Signal, Source, signal_entities
from app.pipeline.scope import in_scope
from app.pipeline.score import band

SHOWN_VERIFICATIONS = (Verification.SUPPORTED, Verification.PARTIAL)


def canonical() -> ColumnElement[bool]:
    return or_(Signal.dedup_cluster_id.is_(None), Signal.dedup_cluster_id == Signal.id)


def visible() -> ColumnElement[bool]:
    return and_(in_scope(), canonical(), Signal.enriched_at.is_not(None))


def event_time() -> ColumnElement[datetime]:
    """When the event happened: publish date, else when we first saw it."""
    return func.coalesce(Signal.published_at, Signal.fetched_at)


def is_page_diff() -> ColumnElement[bool]:
    page_source = (
        select(Source.id)
        .where(Source.id == Signal.source_id, Source.fetch_method == FetchMethod.PAGE_DIFF)
        .exists()
        .correlate_except(Source)
    )
    return or_(Signal.meta["kind"].astext == "page_diff", page_source)


def signals_query() -> Select[tuple[Signal]]:
    return select(Signal).options(selectinload(Signal.source))


def entity_and_products(session: Session, slug: str) -> list[int]:
    """Ids of the entity with this slug and its products ([] if unknown)."""
    root = session.scalar(select(Entity.id).where(Entity.slug == slug))
    if root is None:
        return []
    children = session.scalars(select(Entity.id).where(Entity.parent_id == root)).all()
    return [root, *children]


def entity_refs(session: Session, ids: Sequence[int]) -> dict[int, list[EntityRef]]:
    if not ids:
        return {}
    rows = session.execute(
        select(signal_entities.c.signal_id, signal_entities.c.is_primary, Entity)
        .join(Entity, Entity.id == signal_entities.c.entity_id)
        .where(signal_entities.c.signal_id.in_(ids))
        .order_by(signal_entities.c.is_primary.desc(), Entity.name)
    ).all()
    out: dict[int, list[EntityRef]] = defaultdict(list)
    for signal_id, primary, ent in rows:
        out[signal_id].append(
            EntityRef(slug=ent.slug, name=ent.name, type=ent.type, primary=primary)
        )
    return out


def _kind(sig: Signal) -> str:
    if sig.meta.get("kind") == "page_diff":
        return "page_diff"
    if sig.source is not None and sig.source.fetch_method == FetchMethod.PAGE_DIFF:
        return "page_diff"
    return "article"


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def to_out(sig: Signal, entities: list[EntityRef]) -> SignalOut:
    meta = sig.meta or {}
    kind = _kind(sig)
    evidence = None
    if sig.evidence_quote:
        evidence = EvidenceOut(
            quote=sig.evidence_quote,
            verified=bool(sig.evidence_quote_verified),
            side="before" if meta.get("quote_side") == "before" else "after",
            section=meta.get("quote_section"),
        )
    confidence = None
    if sig.confidence is not None:
        stored = (meta.get("score") or {}).get("band")
        confidence = ConfidenceOut(
            score=round(sig.confidence, 3),
            band=stored
            if stored in ("high", "medium", "low")
            else band(sig.confidence, load_pipeline().score),
        )
    page_diff = None
    if kind == "page_diff":
        origin = meta.get("before_origin")
        page_diff = PageDiffOut(
            before_captured_at=_parse_dt(meta.get("before_captured_at")),
            before_origin=origin if origin in ("wayback", "live") else None,
            after_captured_at=_parse_dt(meta.get("after_captured_at")),
        )
    src = sig.source
    return SignalOut(
        id=sig.id,
        title=sig.title,
        url=sig.url,
        kind=kind,
        source=SourceOut(name=src.name, kind=src.kind, trust=src.trust_score) if src else None,
        published_at=sig.published_at,
        category=sig.category,
        impact=sig.impact,
        is_claim=sig.is_claim,
        summary=sig.summary,
        why_jfrog_cares=sig.why_jfrog_cares,
        evidence=evidence,
        verification=sig.verification,
        confidence=confidence,
        entities=entities,
        sources_count=int((meta.get("dedup") or {}).get("source_count") or 1),
        page_diff=page_diff,
    )


def to_outs(session: Session, signals: Sequence[Signal]) -> list[SignalOut]:
    refs = entity_refs(session, [s.id for s in signals])
    return [to_out(s, refs.get(s.id, [])) for s in signals]
