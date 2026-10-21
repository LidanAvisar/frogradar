"""Competitor Radar API: candidates nobody configured, and the analyst's first write actions.

    GET  /api/radar                 candidates by emergence score (default: new + watching)
    GET  /api/radar/{id}            + profile statements with citations, mentions, merges
    POST /api/radar/{id}/watch      new → watching
    POST /api/radar/{id}/approve    new / watching → approved: creates a tracked entity
                                    (discovered_by radar) and, optionally, a disabled GitHub source
    POST /api/radar/{id}/dismiss    new / watching → dismissed, with a reason code

Every action writes a feedback row (target radar_candidate). No authentication yet (README
"Known limitations"): the analyst is recorded as "anonymous".
Responses never contain raw_text: statements, short contexts and links to the sources only.
"""

import re
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DbSession
from app.api.schemas import (
    CitationRef,
    CreatedEntity,
    CreatedSource,
    RadarApproveIn,
    RadarApproveOut,
    RadarBreakdown,
    RadarCandidateDetail,
    RadarCandidateOut,
    RadarDismissIn,
    RadarListOut,
    RadarMentionOut,
    RadarMergeOut,
    RadarMetrics,
    RadarStatementOut,
    RadarTeaser,
    RadarWatchIn,
)
from app.db.enums import (
    DiscoveredBy,
    EntityReviewStatus,
    EntityType,
    FeedbackAction,
    FeedbackTarget,
    FetchMethod,
    ProfileStatus,
    RadarKind,
    RadarStatus,
    ReasonCode,
    Relationship,
    SourceKind,
)
from app.db.models import Entity, Feedback, RadarCandidate, Signal, Source
from app.pipeline.radar.normalize import repo_slug
from app.pipeline.radar.profile import KEYS, profile_relation
from app.pipeline.synthesis.inputs import display_title

router = APIRouter(prefix="/api/radar", tags=["radar"])

ANALYST = "anonymous"  # no auth yet (README "Known limitations")
OPEN = (RadarStatus.NEW, RadarStatus.WATCHING)
STATUS_ORDER = {s.value: i for i, s in enumerate(RadarStatus)}


# ---------- read ----------


def _live() -> Any:
    return RadarCandidate.merged_into_id.is_(None)


def _line(c: RadarCandidate) -> tuple[str | None, str | None]:
    """A rejected profile (what_it_is failed the checks) supplies no line: fall back to context."""
    ok = c.profile_status == ProfileStatus.OK
    items = ((c.profile or {}).get("items") or {}) if ok else {}
    for key in ("why_notable_now", "what_it_is"):
        text = (items.get(key) or {}).get("statement")
        if text:
            return text, key
    contexts = list(((c.meta or {}).get("contexts") or {}).values())
    return (contexts[0], "context") if contexts else (None, None)


def to_out(c: RadarCandidate) -> RadarCandidateOut:
    line, line_source = _line(c)
    origins = sorted(set(((c.meta or {}).get("mention_origins") or {}).values()))
    metrics = {k: v for k, v in (c.metrics or {}).items() if k in RadarMetrics.model_fields}
    return RadarCandidateOut(
        id=c.id,
        name=c.name,
        kind=c.kind,
        status=c.status,
        homepage=c.homepage,
        repo_url=c.repo_url,
        score=c.score,
        breakdown=RadarBreakdown.model_validate(c.score_breakdown) if c.score_breakdown else None,
        sources_count=len(c.source_ids or []),
        mentions_count=len(c.mention_signal_ids or []),
        first_seen=c.first_seen,
        last_seen=c.last_seen,
        segments=list(c.segments or []),
        line=line,
        line_source=line_source,
        profile_status=c.profile_status,
        origins=origins,
        metrics=RadarMetrics(**metrics),
    )


def _statuses(value: str) -> list[str]:
    out = [v.strip() for v in value.split(",") if v.strip()]
    bad = [v for v in out if v not in STATUS_ORDER]
    if bad or not out:
        raise HTTPException(422, f"unknown status {bad}; allowed: {sorted(STATUS_ORDER)}")
    return out


def _ordered(statuses: list[str]) -> Any:
    return (
        select(RadarCandidate)
        .where(_live(), RadarCandidate.status.in_(statuses))
        .order_by(RadarCandidate.score.desc().nulls_last(), RadarCandidate.id)
    )


@router.get("", response_model=RadarListOut)
def list_candidates(
    session: DbSession,
    status: str = "new,watching",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> RadarListOut:
    statuses = _statuses(status)
    total = session.scalar(
        select(func.count())
        .select_from(RadarCandidate)
        .where(_live(), RadarCandidate.status.in_(statuses))
    )
    rows = session.scalars(_ordered(statuses).limit(limit)).all()
    return RadarListOut(items=[to_out(c) for c in rows], total=total or 0)


def teaser(session: Session, n: int) -> list[RadarTeaser]:
    """Brief card: the top open candidates by score."""
    # A candidate whose profile says it has nothing to do with JFrog ("none") is never teased.
    rows = [
        c
        for c in session.scalars(_ordered([s.value for s in OPEN]).limit(n * 10)).all()
        if profile_relation(c.profile) != "none"
    ][:n]
    return [
        RadarTeaser(
            id=c.id,
            name=c.name,
            status=c.status,
            score=c.score,
            sources_count=len(c.source_ids or []),
            line=_line(c)[0],
        )
        for c in rows
    ]


def _refs(session: Session, ids: list[int]) -> dict[int, tuple[CitationRef, str | None]]:
    if not ids:
        return {}
    rows = session.execute(
        select(Signal, Source.name)
        .outerjoin(Source, Source.id == Signal.source_id)
        .where(Signal.id.in_(sorted(set(ids))))
    ).all()
    return {
        s.id: (
            CitationRef(id=s.id, title=display_title(s), url=s.url, published_at=s.published_at),
            name,
        )
        for s, name in rows
    }


def _merges(c: RadarCandidate) -> list[RadarMergeOut]:
    out = []
    for m in c.merges or []:
        if "from_id" in m:
            out.append(
                RadarMergeOut(
                    name=m.get("from_name", ""),
                    rule=m.get("rule", ""),
                    direction="absorbed",
                    other_id=m["from_id"],
                    at=m.get("at"),
                )
            )
        elif "into_id" in m:
            out.append(
                RadarMergeOut(
                    name=m.get("into_name", ""),
                    rule=m.get("rule", ""),
                    direction="merged_into",
                    other_id=m["into_id"],
                    at=m.get("at"),
                )
            )
        else:
            out.append(
                RadarMergeOut(
                    name=m.get("name", ""),
                    rule=m.get("rule", ""),
                    direction="alias",
                    at=m.get("at"),
                )
            )
    return out


def detail(session: Session, c: RadarCandidate) -> RadarCandidateDetail:
    profile = c.profile or {}
    items = profile.get("items") or {}
    cited = [i for it in items.values() for i in it.get("signal_ids") or []]
    refs = _refs(session, [*cited, *(c.mention_signal_ids or [])])
    names = _product_names(session)
    statements = []
    for key in KEYS:
        it = items.get(key)
        if not it:
            continue
        ids = it.get("signal_ids") or []
        if not ids or not all(i in refs for i in ids):
            continue  # a cited signal is gone: don't show the statement as fact
        statements.append(
            RadarStatementOut(
                key=key,
                text=it.get("statement") or it.get("text", ""),
                citations=[refs[i][0] for i in ids],
                segments=it.get("segments"),
                relation=it.get("relation"),
                products=[names.get(p, p) for p in it["products"]] if it.get("products") else None,
                stage=it.get("stage"),
            )
        )
    origins = (c.meta or {}).get("mention_origins") or {}
    mentions = sorted(
        (
            RadarMentionOut(
                **refs[i][0].model_dump(), source=refs[i][1], origin=origins.get(str(i))
            )
            for i in c.mention_signal_ids or []
            if i in refs
        ),
        key=lambda m: (
            m.published_at is None,
            -(m.published_at.timestamp() if m.published_at else 0),
        ),
    )
    approved = session.get(Entity, c.approved_entity_id) if c.approved_entity_id else None
    generated = profile.get("generated_at")
    return RadarCandidateDetail(
        **to_out(c).model_dump(),
        statements=statements,
        dropped_statements=len(profile.get("dropped") or []),
        profile_generated_at=generated,
        mentions=mentions,
        merges=_merges(c),
        other_names=list((c.meta or {}).get("other_names") or []),
        dismiss_reason=c.dismiss_reason,
        dismiss_comment=c.dismiss_comment,
        approved_entity=approved.slug if approved else None,
    )


def _product_names(session: Session) -> dict[str, str]:
    return dict(session.execute(select(Entity.slug, Entity.name)).all())


def _get(session: Session, candidate_id: int) -> RadarCandidate:
    c = session.get(RadarCandidate, candidate_id)
    if c is None:
        raise HTTPException(404, "No such radar candidate")
    if c.merged_into_id is not None:
        raise HTTPException(404, f"Merged into candidate {c.merged_into_id}")
    return c


@router.get("/{candidate_id}", response_model=RadarCandidateDetail, responses={404: {}})
def get_candidate(candidate_id: int, session: DbSession) -> RadarCandidateDetail:
    return detail(session, _get(session, candidate_id))


# ---------- write ----------


def _open(c: RadarCandidate, action: str) -> None:
    if c.status not in OPEN:
        raise HTTPException(409, f"Cannot {action} a candidate that is {c.status}")


def _feedback(session: Session, c: RadarCandidate, action: FeedbackAction, **kw: Any) -> None:
    session.add(
        Feedback(
            target_type=FeedbackTarget.RADAR_CANDIDATE,
            target_id=c.id,
            action=action,
            analyst=ANALYST,
            **kw,
        )
    )


@router.post(
    "/{candidate_id}/watch", response_model=RadarCandidateDetail, responses={404: {}, 409: {}}
)
def watch(
    candidate_id: int, session: DbSession, body: RadarWatchIn | None = None
) -> RadarCandidateDetail:
    c = _get(session, candidate_id)
    if c.status != RadarStatus.NEW:
        raise HTTPException(409, f"Only new candidates can be watched (this one is {c.status})")
    c.status = RadarStatus.WATCHING
    _feedback(session, c, FeedbackAction.WATCH, comment=body.comment if body else None)
    session.commit()
    return detail(session, c)


@router.post(
    "/{candidate_id}/dismiss", response_model=RadarCandidateDetail, responses={404: {}, 409: {}}
)
def dismiss(candidate_id: int, body: RadarDismissIn, session: DbSession) -> RadarCandidateDetail:
    c = _get(session, candidate_id)
    _open(c, "dismiss")
    c.status = RadarStatus.DISMISSED
    c.dismiss_reason = ReasonCode(body.reason)
    c.dismiss_comment = body.comment
    _feedback(
        session, c, FeedbackAction.REJECT, reason_code=ReasonCode(body.reason), comment=body.comment
    )
    session.commit()
    return detail(session, c)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:90].strip("-")
    return slug or "candidate"


def _unique_slug(session: Session, base: str) -> str:
    taken = set(session.scalars(select(Entity.slug).where(Entity.slug.like(f"{base}%"))))
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


@router.post(
    "/{candidate_id}/approve", response_model=RadarApproveOut, responses={404: {}, 409: {}}
)
def approve(
    candidate_id: int, session: DbSession, body: RadarApproveIn | None = None
) -> RadarApproveOut:
    body = body or RadarApproveIn()
    c = _get(session, candidate_id)
    _open(c, "approve")
    name = (body.name or c.name).strip()
    etype = (
        EntityType(body.type)
        if body.type
        else (EntityType.COMPANY if c.kind == RadarKind.COMPANY else EntityType.PRODUCT)
    )
    statement = ((c.profile or {}).get("items") or {}).get("what_it_is") or {}
    entity = Entity(
        type=etype,
        name=name,
        slug=_unique_slug(session, slugify(name)),
        aliases=sorted({n for n in (c.meta or {}).get("other_names") or [] if n != name}),
        segments=list(c.segments or []),
        website=c.homepage or c.repo_url,
        description=statement.get("statement"),
        relationship=Relationship(body.relationship),
        is_tracked=True,
        review_status=EntityReviewStatus.APPROVED,
        discovered_by=DiscoveredBy.RADAR,
    )
    session.add(entity)
    session.flush()

    sources: list[Source] = []
    slug = repo_slug(c.repo_url)
    if body.add_sources and slug is not None:
        url = f"https://github.com/{slug}"
        if session.scalar(select(Source.id).where(Source.url == url)) is None:
            src = Source(
                entity_id=entity.id,
                name=f"{name} releases (GitHub)",
                url=url,
                kind=SourceKind.GITHUB,
                fetch_method=FetchMethod.GITHUB,
                trust_score=0.85,
                options={"repos": [slug]},
                is_active=False,
                discovered_by=DiscoveredBy.RADAR,
            )
            session.add(src)
            sources.append(src)

    c.status = RadarStatus.APPROVED
    c.approved_entity_id = entity.id
    _feedback(
        session,
        c,
        FeedbackAction.APPROVE,
        comment=body.comment,
        correction={
            "entity_slug": entity.slug,
            "relationship": body.relationship,
            "type": etype.value,
            "name": name,
        },
    )
    session.commit()
    return RadarApproveOut(
        candidate=detail(session, c),
        entity=CreatedEntity(
            slug=entity.slug, name=entity.name, type=entity.type, relationship=entity.relationship
        ),
        sources=[CreatedSource(name=s.name, url=s.url, is_active=s.is_active) for s in sources],
    )
