"""Mission lifecycle outside the agent: prefill from an origin, create, approve the plan, stop,
review findings (approved findings become signals, D73), refresh a radar candidate's profile."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.agents.mission.progress import PROVISIONAL_QUESTIONS, steps_for
from app.core.catalog import MissionSettings, load_discovery, load_llm_routing, load_pipeline
from app.core.llm import llm_context
from app.db.enums import (
    EntityType,
    FeedbackAction,
    FeedbackTarget,
    FindingConfidence,
    FindingStatus,
    MissionOrigin,
    MissionSourceKind,
    MissionStatus,
    ReasonCode,
    ReviewStatus,
    SignalCategory,
    Verification,
)
from app.db.models import (
    AskTurn,
    Capability,
    Entity,
    Feedback,
    Mission,
    MissionFinding,
    RadarCandidate,
    Signal,
    signal_capabilities,
    signal_entities,
)
from app.pipeline.context import CompleteFn, EmbedFn, RunContext
from app.pipeline.hashing import content_hash
from app.pipeline.normalize import canonical_url
from app.pipeline.scope import not_mission, visible
from app.pipeline.score import band as score_band

ACTIVE = (MissionStatus.QUEUED, MissionStatus.PLANNING, MissionStatus.RUNNING)
CONFIDENCE_VALUE = {FindingConfidence.HIGH: 0.85, FindingConfidence.MEDIUM: 0.65,
                    FindingConfidence.LOW: 0.45}  # fmt: skip


class MissionInputError(ValueError):
    """Bad request (→ 4xx)."""


@dataclass
class Prefill:
    brief: str
    origin_kind: MissionOrigin = MissionOrigin.MANUAL
    origin_ref: dict[str, Any] = field(default_factory=dict)
    origin_label: str = ""
    company_ids: list[int] = field(default_factory=list)
    days: int | None = None


def _company_of(session: Session, entity_id: int | None) -> Entity | None:
    e = session.get(Entity, entity_id) if entity_id else None
    while e is not None and e.type != EntityType.COMPANY and e.parent_id:
        e = session.get(Entity, e.parent_id)
    return e


def _primary_company(session: Session, signal_id: int) -> Entity | None:
    rows = session.execute(
        select(signal_entities.c.entity_id, signal_entities.c.is_primary).where(
            signal_entities.c.signal_id == signal_id
        )
    ).all()
    for eid, _ in sorted(rows, key=lambda r: not r.is_primary):
        if (c := _company_of(session, eid)) is not None and c.slug != "jfrog":
            return c
    return None


def prefill(
    session: Session,
    *,
    signal: int | None = None,
    candidate: int | None = None,
    entity: int | None = None,
    capability: int | None = None,
    turn: int | None = None,
) -> Prefill:
    if signal is not None:
        sig = session.get(Signal, signal)
        if sig is None:
            raise MissionInputError(f"No signal {signal}")
        company = _primary_company(session, sig.id)
        who = f"{company.name}: " if company else ""
        return Prefill(
            brief=f'Investigate "{who}{sig.title}". What exactly changed, what is confirmed by '
            "official sources, and what does it mean for JFrog's products?",
            origin_kind=MissionOrigin.SIGNAL,
            origin_ref={"signal_id": sig.id},
            origin_label=f"Signal #{sig.id}: {sig.title}"[:200],
            company_ids=[company.id] if company else [],
        )
    if candidate is not None:
        cand = session.get(RadarCandidate, candidate)
        if cand is None:
            raise MissionInputError(f"No radar candidate {candidate}")
        return Prefill(
            brief=f"What does {cand.name} sell, and how does it overlap with JFrog's products?",
            origin_kind=MissionOrigin.RADAR_CANDIDATE,
            origin_ref={"candidate_id": cand.id},
            origin_label=f"Radar candidate: {cand.name}",
        )
    if entity is not None:
        company = _company_of(session, entity)
        if company is None:
            raise MissionInputError(f"No company {entity}")
        if capability is not None:
            cap = session.get(Capability, capability)
            if cap is None:
                raise MissionInputError(f"No capability {capability}")
            return Prefill(
                brief=f"Does {company.name} offer {cap.name}?",
                origin_kind=MissionOrigin.CAPABILITY,
                origin_ref={"entity_id": company.id, "capability_id": cap.id},
                origin_label=f"Battlecard {company.name} · {cap.name}",
                company_ids=[company.id],
            )
        return Prefill(
            brief=f"What has {company.name} launched or changed in the last 90 days, and where "
            "does it compete with JFrog?",
            origin_kind=MissionOrigin.MANUAL,
            origin_ref={"entity_id": company.id},
            origin_label=f"Battlecard {company.name}",
            company_ids=[company.id],
            days=90,
        )
    if turn is not None:
        t = session.get(AskTurn, turn)
        if t is None:
            raise MissionInputError(f"No Ask turn {turn}")
        gap = str((t.answer or {}).get("gap") or "").strip()
        brief = t.standalone_question or t.question
        if gap:
            brief += f"\n\nWhat FrogRadar could not answer: {gap}"
        slug = (t.filters or {}).get("company_slug")
        company = session.scalar(select(Entity).where(Entity.slug == slug)) if slug else None
        return Prefill(
            brief=brief[:2000],
            origin_kind=MissionOrigin.ASK_TURN,
            origin_ref={"turn_id": t.id},
            origin_label=f"Ask: {t.question}"[:200],
            company_ids=[company.id] if company else [],
        )
    return Prefill(brief="")


def create_mission(
    session: Session,
    *,
    brief: str,
    created_by: str,
    origin_kind: MissionOrigin,
    origin_ref: dict[str, Any],
    company_ids: list[int],
    days: int | None,
    budget_usd: float | None,
    cfg: MissionSettings | None = None,
) -> Mission:
    cfg = cfg or load_pipeline().missions
    brief = brief.strip()
    if len(brief) < 10:
        raise MissionInputError("Write a research brief of at least 10 characters.")
    budget = budget_usd if budget_usd is not None else cfg.default_budget_usd
    if not 0 < budget <= cfg.max_budget_usd:
        raise MissionInputError(f"Budget must be between $0.01 and ${cfg.max_budget_usd:.2f}.")
    companies = []
    for cid in company_ids:
        c = _company_of(session, cid)
        if c is None:
            raise MissionInputError(f"No company {cid}")
        companies.append(c)
    entity_id = companies[0].id if companies else None
    capability_id = None
    candidate_id = None
    if origin_kind == MissionOrigin.CAPABILITY:
        capability_id = origin_ref.get("capability_id")
        entity_id = origin_ref.get("entity_id") or entity_id
        if capability_id is None or session.get(Capability, capability_id) is None:
            raise MissionInputError("Capability mission without a valid capability_id")
    if origin_kind == MissionOrigin.RADAR_CANDIDATE:
        candidate_id = origin_ref.get("candidate_id")
        if candidate_id is None or session.get(RadarCandidate, candidate_id) is None:
            raise MissionInputError("Radar mission without a valid candidate_id")
    if (
        origin_kind == MissionOrigin.SIGNAL
        and session.get(Signal, origin_ref.get("signal_id") or -1) is None
    ):
        raise MissionInputError("Signal mission without a valid signal_id")
    if (
        origin_kind == MissionOrigin.ASK_TURN
        and session.get(AskTurn, origin_ref.get("turn_id") or -1) is None
    ):
        raise MissionInputError("Ask mission without a valid turn_id")
    scope: dict[str, Any] = {
        "company_ids": [c.id for c in companies],
        "company_names": [c.name for c in companies],
    }
    if candidate_id is not None:
        cand = session.get(RadarCandidate, candidate_id)
        assert cand is not None
        scope["company_names"] = [cand.name, *scope["company_names"]]
    if days:
        if not 1 <= days <= 3650:
            raise MissionInputError("Time window must be 1-3650 days.")
        scope["days"] = days
    m = Mission(
        title=" ".join(brief.split())[:120],
        brief=brief[:4000],
        scope=scope,
        budget_usd=Decimal(str(round(budget, 2))),
        # Provisional until the plan is approved; then it follows the plan (D84).
        max_steps=steps_for(PROVISIONAL_QUESTIONS, cfg),
        status=MissionStatus.QUEUED,
        created_by=(created_by.strip() or "analyst")[:100],
        origin_kind=origin_kind,
        origin_ref=origin_ref,
        entity_id=entity_id,
        capability_id=capability_id,
        radar_candidate_id=candidate_id,
        timeout_s=cfg.timeout_s,
    )
    session.add(m)
    session.flush()
    return m


def approve_plan(session: Session, m: Mission, sub_questions: list[dict[str, str]] | None,
                 analyst: str, cfg: MissionSettings | None = None) -> None:  # fmt: skip
    if m.status != MissionStatus.AWAITING_APPROVAL:
        raise MissionInputError(
            f"The plan can only be approved while awaiting approval ({m.status})."
        )  # noqa: E501
    plan = dict(m.plan or {})
    if sub_questions is not None:
        cleaned = []
        for q in sub_questions:
            text = " ".join(str(q.get("question") or "").split())
            if len(text) < 5:
                continue
            query = " ".join(str(q.get("search_query") or "").split()) or text[:200]
            cleaned.append({"question": text[:300], "search_query": query[:200],
                            "why": str(q.get("why") or "")[:300]})  # fmt: skip
        if not 1 <= len(cleaned) <= 6:
            raise MissionInputError("A plan needs 1 to 6 sub-questions.")
        if cleaned != plan.get("sub_questions"):
            plan["edited_by_analyst"] = True
        plan["sub_questions"] = cleaned
    plan["approved"] = True
    plan["approved_by"] = (analyst.strip() or "analyst")[:100]
    m.plan = plan
    # The step limit follows the approved plan (search / read / extract / verify per
    # sub-question); budget, time and Stop stay the real guards (D84).
    m.max_steps = steps_for(len(plan.get("sub_questions") or []), cfg or load_pipeline().missions)
    m.status = MissionStatus.QUEUED


def stop_mission(session: Session, m: Mission, now: datetime) -> None:
    if m.status in (MissionStatus.QUEUED, MissionStatus.AWAITING_APPROVAL):
        m.status = MissionStatus.CANCELLED
        m.error = "Stopped by the analyst."
        m.finished_at = now
    elif m.status in (MissionStatus.PLANNING, MissionStatus.RUNNING):
        m.stop_requested = True
    else:
        raise MissionInputError(f"Mission is already {m.status}.")


def _feedback(session: Session, f: MissionFinding, action: FeedbackAction, analyst: str,
              reason: ReasonCode | None = None, comment: str | None = None) -> None:  # fmt: skip
    session.add(
        Feedback(
            target_type=FeedbackTarget.MISSION_FINDING,
            target_id=f.id,
            action=action,
            reason_code=reason,
            comment=comment,
            analyst=(analyst.strip() or "analyst")[:100],
        )  # fmt: skip
    )


def _add_mention(session: Session, m: Mission, signal_id: int) -> None:
    if m.radar_candidate_id is None:
        return
    cand = session.get(RadarCandidate, m.radar_candidate_id)
    if cand is not None and signal_id not in (cand.mention_signal_ids or []):
        cand.mention_signal_ids = [*(cand.mention_signal_ids or []), signal_id]


def approve_finding(
    session: Session, f: MissionFinding, analyst: str, now: datetime, embed: EmbedFn | None
) -> int:
    """Approve: the finding becomes a signal (or, for a FrogRadar source, is linked to the signal
    it came from). Returns the signal id."""
    if f.status not in (FindingStatus.NEW, FindingStatus.REJECTED):
        raise MissionInputError(f"Finding {f.id} can't be approved ({f.status}).")
    m = session.get(Mission, f.mission_id)
    assert m is not None
    src = f.source
    if src.kind == MissionSourceKind.KB and src.signal_id is not None:
        signal_id = src.signal_id  # already intelligence: link, don't duplicate
    else:
        vector, model = _embed_finding(m, f, embed)
        existing = _existing_signal(session, f, vector, model)
        if existing is not None:
            signal_id = existing  # the same story is already a signal: link, don't duplicate (D82)
        else:
            signal_id = _new_signal(session, m, f, now, vector, model)
    f.status = FindingStatus.APPROVED
    f.signal_id = signal_id
    f.reviewed_by = (analyst.strip() or "analyst")[:100]
    f.reviewed_at = now
    _add_mention(session, m, signal_id)
    _feedback(session, f, FeedbackAction.APPROVE, analyst)
    return signal_id


def _embedding_text(f: MissionFinding) -> str:
    return f"{f.statement[:300]}\n{f.statement}"  # the new signal's title + summary


def _embed_finding(m: Mission, f: MissionFinding,
                   embed: EmbedFn | None) -> tuple[list[float] | None, str | None]:  # fmt: skip
    if embed is None:
        return None, None
    try:
        with llm_context(mission_id=m.id):
            res = embed([_embedding_text(f)])
        return list(res.vectors[0]), res.model
    except Exception:  # noqa: BLE001 - full-text search still finds it; embed later
        return None, None


def _existing_signal(session: Session, f: MissionFinding, vector: list[float] | None,
                     model: str | None) -> int | None:  # fmt: skip
    """A visible signal that already tells this finding's story (D82), else None:
    1. a collected signal (not another finding) of the same page: one page often yields several
       distinct findings, so findings are never matched to each other by URL;
    2. any visible signal whose embedding reaches dedup.cross_source_threshold (needs an
       embedding: without one, e.g. without an embedding key, only the URL check runs)."""
    url = canonical_url(f.source.url)
    same_page = session.scalar(
        select(Signal.id)
        .where(visible(), not_mission(), Signal.canonical_url == url)
        .order_by(Signal.id)
        .limit(1)
    )
    if same_page is not None:
        return same_page
    if vector is None:
        return None
    threshold = load_pipeline().dedup.cross_source_threshold
    distance = Signal.embedding.cosine_distance(vector)
    return session.scalar(
        select(Signal.id)
        .where(visible(), Signal.embedding_model == model, distance <= 1 - threshold)
        .order_by(distance, Signal.id)
        .limit(1)
    )


def _new_signal(session: Session, m: Mission, f: MissionFinding, now: datetime,
                vector: list[float] | None, model: str | None) -> int:  # fmt: skip
    src = f.source
    meta = f.meta or {}
    try:
        category = SignalCategory(meta.get("category") or "other")
    except ValueError:
        category = SignalCategory.OTHER
    confidence = CONFIDENCE_VALUE[f.confidence]
    if f.verification != Verification.SUPPORTED:
        confidence = min(confidence, CONFIDENCE_VALUE[FindingConfidence.MEDIUM])
    band = score_band(confidence, load_pipeline().score)
    summary = f.statement
    sig = Signal(
        source_id=None,
        mission_id=m.id,
        url=src.url,
        canonical_url=canonical_url(src.url),
        content_hash=content_hash(f"mission {m.id} finding {f.id}: {f.statement}"),
        title=f.statement[:300],
        raw_text=src.excerpt,  # analysis only (enrich / RAG context), never displayed
        published_at=None,
        fetched_at=src.fetched_at,
        meta={
            "kind": "mission",
            "mission_id": m.id,
            "finding_id": f.id,
            "source_title": src.title,
            "score": {"confidence": confidence, "band": band},
        },
        relevant=True,
        category=category,
        summary=summary,
        why_jfrog_cares=(meta.get("why_jfrog_cares") or None),
        takeaway=f.statement[:160],
        impact=f.impact or 2,
        confidence=confidence,
        evidence_quote=f.evidence_quote,
        evidence_quote_verified=True,
        verification=f.verification,
        verification_notes=f.verification_notes,
        is_claim=f.is_claim,
        status=ReviewStatus.APPROVED,
        enriched_at=now,
        enrich_prompt_version="mission_extract.v1",
    )
    session.add(sig)
    session.flush()
    if m.entity_id is not None:
        session.execute(
            insert(signal_entities).values(signal_id=sig.id, entity_id=m.entity_id, is_primary=True)
        )
    if m.capability_id is not None:
        session.execute(
            insert(signal_capabilities).values(signal_id=sig.id, capability_id=m.capability_id)
        )
    if vector is not None:
        sig.embedding = vector
        sig.embedding_model = model
    return sig.id


def reject_finding(session: Session, f: MissionFinding, analyst: str, now: datetime,
                   reason: ReasonCode | None, comment: str | None) -> None:  # fmt: skip
    if f.status not in (FindingStatus.NEW, FindingStatus.APPROVED):
        raise MissionInputError(f"Finding {f.id} can't be rejected ({f.status}).")
    if f.status == FindingStatus.APPROVED and f.signal_id is not None:
        sig = session.get(Signal, f.signal_id)
        # Only the signal this finding created: a finding linked to an existing signal (KB source
        # or the same story, D82) leaves that signal alone.
        if sig is not None and sig.mission_id == f.mission_id and _created_by(sig, f):
            sig.status = ReviewStatus.REJECTED  # leaves visible() like any rejected signal
        m = session.get(Mission, f.mission_id)
        if m is not None and m.radar_candidate_id is not None:
            cand = session.get(RadarCandidate, m.radar_candidate_id)
            if cand is not None:
                cand.mention_signal_ids = [i for i in cand.mention_signal_ids if i != f.signal_id]
    f.status = FindingStatus.REJECTED
    f.reviewed_by = (analyst.strip() or "analyst")[:100]
    f.reviewed_at = now
    _feedback(session, f, FeedbackAction.REJECT, analyst, reason, comment)


def _created_by(sig: Signal, f: MissionFinding) -> bool:
    return (sig.meta or {}).get("finding_id") == f.id


def radar_snapshot(cand: RadarCandidate) -> dict[str, Any]:
    from app.pipeline.radar.profile import profile_relation

    return {
        "profile_status": str(cand.profile_status),
        "relation": profile_relation(cand.profile),
        "score": cand.score,
        "mentions": len(cand.mention_signal_ids or []),
        "statements": sorted(((cand.profile or {}).get("items") or {}).keys()),
    }


def refresh_radar_profile(
    session: Session, m: Mission, complete: CompleteFn, embed: EmbedFn, now: datetime
) -> dict[str, Any]:
    """Rebuild the candidate's profile from its mentions (now incl. approved findings) and
    re-score it. Counted to the daily budget (a radar operation), not to the mission."""
    from app.pipeline.radar.profile import build_profile
    from app.pipeline.radar.step import refresh_score

    if m.radar_candidate_id is None:
        raise MissionInputError("This mission is not about a radar candidate.")
    cand = session.get(RadarCandidate, m.radar_candidate_id)
    if cand is None:
        raise MissionInputError("The radar candidate no longer exists.")
    cfg = load_discovery().radar
    before = radar_snapshot(cand)
    ctx = RunContext(
        run_id=None,
        config=load_pipeline(),
        embedding_model=load_llm_routing().embedding.model,
        embed=embed,
        complete=complete,
        now=now,
    )
    status = build_profile(
        session, cand, cfg.profile, complete, now, model=load_llm_routing().tasks.bulk.model
    )
    refresh_score(session, cand, cfg, ctx)
    session.flush()
    return {"status": status, "before": before, "after": radar_snapshot(cand)}
