"""GET /api/entities/{slug}/battlecard – the latest battlecard of a competitor, its capability
matrix (JFrog vs competitor), a timeline of its signals and coverage counts (D54).

A card is shown only while the company has enough evidence (D86): `min_primary_signals` usable
signals about it in the last `lookback_days`, the pipeline's own rule. Below that the card is
withheld (also one built earlier); the matrix and timeline still show, each with its evidence.

Read-time check: a card item is shown only while every signal it cites is still usable; a matrix
level whose evidence signal is no longer usable is shown as unknown. JFrog's levels come from
config (jfrog_capabilities.yaml), never from the LLM. The timeline is the signals themselves.
"""

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import DbSession, Now
from app.api.entities import list_entities
from app.api.queries import citation_refs, event_time, usable
from app.api.schemas import (
    BattlecardEntity,
    BattlecardListItem,
    BattlecardOut,
    CapabilityRef,
    CardEvidence,
    CitationRef,
    CitedText,
    CompetitorLevel,
    Coverage,
    JFrogLevel,
    MatrixRow,
    TimelineItem,
)
from app.core.catalog import BattlecardSettings, load_pipeline, load_self_capabilities
from app.db.enums import CapabilityLevel, EntityType, ReportKind
from app.db.models import Capability, CapabilityMatrix, Entity, Report, Signal, signal_entities
from app.pipeline.synthesis.battlecards import card_settings, evidence_counts
from app.pipeline.synthesis.inputs import display_title
from app.pipeline.synthesis.schemas import SECTIONS

router = APIRouter(prefix="/api", tags=["entities"])

TIMELINE_MAX = 30
HIGH_IMPACT = 4


@router.get("/battlecards", response_model=list[BattlecardListItem])
def list_battlecards(session: DbSession, now: Now) -> list[BattlecardListItem]:
    """Tracked competitors for the switcher: those with a card first (by signal count), then the
    rest (no card yet, or not enough evidence for one)."""
    cfg = card_settings()
    about = evidence_counts(session, now, cfg)
    latest = dict(
        session.execute(
            select(Entity.slug, func.max(Report.created_at))
            .join(Report, Report.entity_id == Entity.id)
            .where(Report.kind == ReportKind.BATTLECARD)
            .group_by(Entity.slug)
        ).all()
    )
    items = []
    for e in list_entities(session):
        if e.is_self:
            continue
        evidence = _evidence(about.get(e.id, 0), cfg)
        items.append(
            BattlecardListItem(
                slug=e.slug,
                name=e.name,
                relationship=e.relationship,
                signal_count=e.signal_count,
                generated_at=latest.get(e.slug) if evidence.enough else None,
                evidence=evidence,
            )
        )
    # Stable sort keeps list_entities' order (signal count, then name) within each group.
    return sorted(items, key=lambda i: i.generated_at is None)


@router.get("/entities/{slug}/battlecard", response_model=BattlecardOut)
def get_battlecard(slug: str, session: DbSession, now: Now) -> BattlecardOut:
    ent = session.scalar(
        select(Entity).where(Entity.slug == slug, Entity.type == EntityType.COMPANY)
    )
    if ent is None or ent.is_self:
        raise HTTPException(404, f"no competitor {slug!r}")
    entity_ids = [ent.id, *session.scalars(select(Entity.id).where(Entity.parent_id == ent.id))]
    cfg = card_settings()
    evidence = _evidence(evidence_counts(session, now, cfg).get(ent.id, 0), cfg)
    report = (
        session.scalar(
            select(Report)
            .where(Report.kind == ReportKind.BATTLECARD, Report.entity_id == ent.id)
            .order_by(Report.created_at.desc(), Report.id.desc())
            .limit(1)
        )
        if evidence.enough
        else None
    )
    content: dict[str, Any] = report.content if report else {}

    raw_items = {name: _items(content.get(name)) for name in SECTIONS}
    positioning = _items([content.get("positioning")]) if content.get("positioning") else []
    cited = [
        i for items in [positioning, *raw_items.values()] for it in items for i in it.signal_ids
    ]

    matrix, evidence_ids = _matrix(session, ent, now)
    refs = citation_refs(session, [*cited, *evidence_ids])

    def shown(items: list[CitedText]) -> list[CitedText]:
        return [it for it in items if all(i in refs for i in it.signal_ids)]

    sections = {name: shown(items) for name, items in raw_items.items()}
    kept_positioning = shown(positioning)
    final_matrix = [_with_evidence(row, refs) for row in matrix]
    used = sorted(
        {
            i
            for items in [kept_positioning, *sections.values()]
            for it in items
            for i in it.signal_ids
        }
    )
    return BattlecardOut(
        entity=BattlecardEntity(
            slug=ent.slug,
            name=ent.name,
            relationship=ent.relationship,
            segments=list(ent.segments or []),
        ),
        evidence=evidence,
        report_id=report.id if report else None,
        generated_at=report.created_at if report else None,
        model=report.model if report else None,
        positioning=kept_positioning[0] if kept_positioning else None,
        **sections,
        citations=[refs[i] for i in used],
        matrix=final_matrix,
        timeline=_timeline(session, entity_ids),
        coverage=_coverage(session, entity_ids, final_matrix),
    )


def _evidence(n: int, cfg: BattlecardSettings) -> CardEvidence:
    return CardEvidence(
        signals_about=n,
        required=cfg.min_primary_signals,
        lookback_days=cfg.lookback_days,
        enough=n >= cfg.min_primary_signals,
    )


def _items(value: Any) -> list[CitedText]:
    out = []
    for item in value or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            ids = [i for i in item.get("signal_ids") or [] if isinstance(i, int)]
            if ids:
                out.append(CitedText(text=item["text"], signal_ids=ids))
    return out


def _matrix(session: DbSession, ent: Entity, now: datetime) -> tuple[list[MatrixRow], list[int]]:
    """Rows for the capabilities in the competitor's segments (plus any with a known level)."""
    days = load_pipeline().synthesis.battlecards.changed_recently_days
    jfrog = load_self_capabilities().capabilities
    cells = {
        c.capability_id: c
        for c in session.scalars(
            select(CapabilityMatrix).where(CapabilityMatrix.entity_id == ent.id)
        )
    }
    caps = session.scalars(select(Capability).order_by(Capability.segment, Capability.slug)).all()
    rows, evidence = [], []
    for cap in caps:
        cell = cells.get(cap.id)
        if cap.segment not in (ent.segments or []) and cell is None:
            continue
        mine = jfrog.get(cap.slug)
        changed = bool(
            cell
            and cell.level_changed_at
            and cell.previous_level not in (None, CapabilityLevel.UNKNOWN)
            and cell.level_changed_at >= now - timedelta(days=days)
        )
        if cell and cell.evidence_signal_id:
            evidence.append(cell.evidence_signal_id)
        rows.append(
            MatrixRow(
                capability=CapabilityRef(slug=cap.slug, name=cap.name, segment=cap.segment),
                jfrog=JFrogLevel(
                    level=mine.level if mine else "unknown",
                    note=mine.note if mine else "Not in jfrog_capabilities.yaml.",
                    source=mine.source if mine else None,
                ),
                competitor=CompetitorLevel(
                    level=cell.level if cell else "unknown",
                    note=cell.notes if cell else None,
                    evidence=(
                        CitationRef(id=cell.evidence_signal_id, title="", url="", published_at=None)
                        if cell and cell.evidence_signal_id
                        else None
                    ),
                    last_verified_at=cell.last_verified_at if cell else None,
                ),
                changed_recently=changed,
            )
        )
    return rows, evidence


def _with_evidence(row: MatrixRow, refs: dict[int, CitationRef]) -> MatrixRow:
    """Fill the evidence link; a level whose evidence is no longer usable is shown as unknown."""
    ev = row.competitor.evidence
    if ev is None:
        if row.competitor.level != "unknown":  # a level without evidence is never shown
            return row.model_copy(
                update={
                    "competitor": CompetitorLevel(
                        level="unknown", note=None, evidence=None, last_verified_at=None
                    )
                }  # fmt: skip
            )
        return row
    if ev.id not in refs:
        return row.model_copy(
            update={
                "competitor": CompetitorLevel(
                    level="unknown", note=None, evidence=None, last_verified_at=None
                ),
                "changed_recently": False,
            }
        )
    return row.model_copy(
        update={"competitor": row.competitor.model_copy(update={"evidence": refs[ev.id]})}
    )


def _linked(entity_ids: list[int]) -> Any:
    return (
        select(signal_entities.c.signal_id)
        .where(signal_entities.c.entity_id.in_(entity_ids))
        .scalar_subquery()
    )


def _timeline(session: DbSession, entity_ids: list[int]) -> list[TimelineItem]:
    rows = session.scalars(
        select(Signal)
        .where(usable(), Signal.id.in_(_linked(entity_ids)))
        .order_by(event_time().desc(), Signal.id.desc())
        .limit(TIMELINE_MAX)
    ).all()
    return [
        TimelineItem(
            id=s.id,
            date=s.published_at or s.fetched_at,
            title=display_title(s),
            category=s.category,
            impact=s.impact,
            url=s.url,
            research=(s.meta or {}).get("kind") == "mission",
        )
        for s in rows
    ]


def _coverage(session: DbSession, entity_ids: list[int], matrix: list[MatrixRow]) -> Coverage:
    n, high, sources, first, last = session.execute(
        select(
            func.count(),
            func.count().filter(Signal.impact >= HIGH_IMPACT),
            func.count(func.distinct(Signal.source_id)),
            func.min(event_time()),
            func.max(event_time()),
        ).where(usable(), Signal.id.in_(_linked(entity_ids)))
    ).one()
    return Coverage(
        signals=n,
        high_impact=high,
        sources=sources,
        capabilities_known=sum(r.competitor.level != "unknown" for r in matrix),
        capabilities_total=len(matrix),
        first_signal_at=first,
        last_signal_at=last,
    )
