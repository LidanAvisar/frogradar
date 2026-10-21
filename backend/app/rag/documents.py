"""What Ask can retrieve: usable signals, battlecard items and accepted radar profiles (D65).

Each document has a typed, stable id the answer model cites ("sig:1055", "bc:sonatype:weaknesses:1",
"radar:158") and a short rendered text built from enrichment outputs only. raw_text is never read:
signal queries select explicit columns.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.api.radar import _relation_source
from app.db.enums import ProfileStatus, RadarStatus, ReportKind
from app.db.models import Entity, RadarCandidate, Report, Signal, Source, signal_entities
from app.pipeline.radar.profile import KEYS as PROFILE_KEYS
from app.pipeline.scope import event_time, usable
from app.pipeline.synthesis.schemas import SECTIONS

DocType = Literal["article", "pricing_page", "battlecard", "radar"]

SECTION_LABELS = {
    "positioning": "Positioning",
    "strengths": "Strength",
    "weaknesses": "Weakness",
    "opportunities_for_jfrog": "Opportunity for JFrog",
    "blind_spots_for_jfrog": "Blind spot for JFrog",
}


@dataclass(frozen=True)
class Doc:
    id: str
    type: DocType
    title: str
    text: str  # what the answer and verify models see (no raw_text)
    url: str
    date: datetime | None = None
    source: str | None = None
    source_kind: str | None = None
    confidence: float | None = None
    band: str | None = None
    entity_ids: frozenset[int] = field(default_factory=frozenset)
    signal_id: int | None = None
    cites: tuple[int, ...] = ()  # battlecard items: the signals the item cites

    def render(self, max_chars: int) -> str:
        head = f"[{self.id}] type: {self.type}"
        if self.date:
            head += f" | date: {self.date.date().isoformat()}"
        if self.source:
            head += f" | source: {self.source}"
        body = self.text if len(self.text) <= max_chars else self.text[: max_chars - 1] + "…"
        return f"{head}\n{body}"


def _signal_columns() -> Select[Any]:
    # Explicit columns: raw_text is never loaded.
    return select(
        Signal.id,
        Signal.title,
        Signal.url,
        Signal.published_at,
        Signal.fetched_at,
        Signal.category,
        Signal.impact,
        Signal.confidence,
        Signal.summary,
        Signal.takeaway,
        Signal.why_jfrog_cares,
        Signal.evidence_quote,
        Signal.evidence_quote_verified,
        Signal.meta,
        Source.name.label("source_name"),
        Source.kind.label("source_kind"),
        Source.fetch_method.label("fetch_method"),
    ).outerjoin(Source, Source.id == Signal.source_id)


def signal_docs(session: Session, ids: list[int]) -> dict[int, Doc]:
    """Usable signals by id, as documents."""
    if not ids:
        return {}
    rows = session.execute(_signal_columns().where(Signal.id.in_(ids), usable())).all()
    links: dict[int, set[int]] = {}
    for sid, eid in session.execute(
        select(signal_entities.c.signal_id, signal_entities.c.entity_id).where(
            signal_entities.c.signal_id.in_(ids)
        )
    ):
        links.setdefault(sid, set()).add(eid)
    out = {}
    for r in rows:
        meta = r.meta or {}
        is_diff = meta.get("kind") == "page_diff" or str(r.fetch_method) == "page_diff"
        title = meta.get("display_title") or r.title
        lines = [f"Title: {title}"]
        if r.category:
            lines.append(f"Category: {r.category} | impact {r.impact}")
        if r.takeaway:
            lines.append(f"Takeaway: {r.takeaway}")
        if r.summary:
            lines.append(f"Summary: {r.summary}")
        if r.why_jfrog_cares:
            lines.append(f"Why JFrog cares: {r.why_jfrog_cares}")
        if r.evidence_quote and r.evidence_quote_verified:
            lines.append(f'Evidence quote: "{r.evidence_quote}"')
        out[r.id] = Doc(
            id=f"sig:{r.id}",
            type="pricing_page" if is_diff else "article",
            title=title,
            text="\n".join(lines),
            url=r.url,
            date=r.published_at or r.fetched_at,
            source=r.source_name,
            source_kind=str(r.source_kind) if r.source_kind else None,
            confidence=r.confidence,
            band=(meta.get("score") or {}).get("band"),
            entity_ids=frozenset(links.get(r.id, set())),
            signal_id=r.id,
        )
    return out


def signal_id_of(doc_id: str) -> int | None:
    return int(doc_id[4:]) if doc_id.startswith("sig:") and doc_id[4:].isdigit() else None


def battlecard_docs(session: Session) -> list[Doc]:
    """Items of each competitor's latest battlecard whose cited signals are all still usable
    (the battlecard API's read-time rule)."""
    reports = session.scalars(
        select(Report)
        .where(Report.kind == ReportKind.BATTLECARD, Report.entity_id.is_not(None))
        .order_by(Report.entity_id, Report.created_at.desc(), Report.id.desc())
        .ext(distinct_on(Report.entity_id))
    ).all()
    if not reports:
        return []
    entities = {e.id: e for e in session.scalars(select(Entity))}
    cited = {
        i
        for r in reports
        for it in _items(r.content)
        for i in it[2]  # signal ids
    }
    ok = set(session.scalars(select(Signal.id).where(Signal.id.in_(cited), usable())))
    out = []
    for r in reports:
        ent = entities.get(r.entity_id or -1)
        if ent is None:
            continue
        family = frozenset([ent.id, *(e.id for e in entities.values() if e.parent_id == ent.id)])
        for section, idx, ids, text in _items(r.content):
            if not ids or not all(i in ok for i in ids):
                continue
            label = SECTION_LABELS.get(section, section)
            out.append(
                Doc(
                    id=f"bc:{ent.slug}:{section}:{idx}",
                    type="battlecard",
                    title=f"{ent.name} battlecard · {label}",
                    text=f"{ent.name} battlecard, {label.lower()}: {text}",
                    url=f"/battlecards/{ent.slug}",
                    date=r.created_at,
                    source="FrogRadar battlecard",
                    entity_ids=family,
                    cites=tuple(ids),
                )
            )
    return out


def _items(content: dict[str, Any] | None) -> list[tuple[str, int, list[int], str]]:
    content = content or {}
    out = []
    sections: list[tuple[str, Any]] = [("positioning", [content.get("positioning")])]
    sections += [(name, content.get(name)) for name in SECTIONS]
    for name, items in sections:
        for idx, it in enumerate(items or [], start=1):
            if isinstance(it, dict) and isinstance(it.get("text"), str):
                ids = [i for i in it.get("signal_ids") or [] if isinstance(i, int)]
                out.append((name, idx, ids, it["text"]))
    return out


STATEMENT_LABELS = {
    "what_it_is": "What it is",
    "segments": "Segments",
    "jfrog_relation": "JFrog relation",
    "stage": "Stage",
    "why_notable_now": "Why notable now",
}


def radar_docs(session: Session) -> list[Doc]:
    """Open or tracked radar candidates with an accepted profile: name, kind and the statements
    that passed the profile checks."""
    rows = session.scalars(
        select(RadarCandidate).where(
            RadarCandidate.merged_into_id.is_(None),
            RadarCandidate.profile_status == ProfileStatus.OK,
            RadarCandidate.status.in_(
                [RadarStatus.NEW, RadarStatus.WATCHING, RadarStatus.APPROVED]
            ),
        )
    ).all()
    product_names = dict(session.execute(select(Entity.slug, Entity.name)).all())
    out = []
    for c in rows:
        items = (c.profile or {}).get("items") or {}
        lines = [f"{c.name} ({str(c.kind).replace('_', ' ')}), found by the Competitor Radar"]
        for key in PROFILE_KEYS:
            st = items.get(key) or {}
            text = st.get("statement") or st.get("text")
            if text:
                lines.append(f"{STATEMENT_LABELS[key]}: {text}")
        if len(lines) == 1:
            continue
        relation = (c.metrics or {}).get("relation")
        if relation and "jfrog_relation" not in items:
            products, check = _relation_source(c)
            names = ", ".join(product_names.get(p, p) for p in products)
            note = " (statement only partly verified)" if check == "partial" else ""
            lines.append(f"JFrog relation: {relation}{' – ' + names if names else ''}{note}")
        out.append(
            Doc(
                id=f"radar:{c.id}",
                type="radar",
                title=f"{c.name} (Competitor Radar)",
                text="\n".join(lines),
                url=f"/radar/{c.id}",
                date=c.first_seen,
                source="FrogRadar Competitor Radar",
            )
        )
    return out


__all__ = [
    "Doc",
    "DocType",
    "battlecard_docs",
    "event_time",
    "radar_docs",
    "signal_docs",
    "signal_id_of",
]
