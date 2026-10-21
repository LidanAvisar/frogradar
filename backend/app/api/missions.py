"""Research missions API (S15).

    GET  /api/missions/prefill?signal=|candidate=|entity=[&capability=]|turn=   form defaults
    POST /api/missions                       create; the worker writes the plan
    GET  /api/missions                       list (status, origin, cost)
    GET  /api/missions/{id}                  plan, steps, findings, report (polled while running)
    POST /api/missions/{id}/plan             approve (optionally edited) plan → research starts
    POST /api/missions/{id}/stop             stop (cooperative: between steps)
    POST /api/missions/findings/{id}/approve finding → signal (D73)
    POST /api/missions/findings/{id}/reject
    POST /api/missions/{id}/refresh-radar    rebuild the radar candidate's profile (1-2 LLM calls)

Responses never contain fetched page text (mission_sources.excerpt) or signals.raw_text: findings
show the statement, a short evidence quote and the source link.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.mission.runner import COUNTED_KINDS
from app.agents.mission.service import (
    MissionInputError,
    approve_finding,
    approve_plan,
    create_mission,
    prefill,
    refresh_radar_profile,
    reject_finding,
    stop_mission,
)
from app.agents.mission.worker import MissionWorker, get_worker
from app.api.ask import Llm
from app.api.deps import DbSession, Now
from app.api.schemas import (
    FindingReviewIn,
    FindingReviewOut,
    MissionCreateIn,
    MissionDetailOut,
    MissionFindingOut,
    MissionListOut,
    MissionOut,
    MissionPlanIn,
    MissionPlanOut,
    MissionPrefillOut,
    MissionReportOut,
    MissionSourceOut,
    MissionStepOut,
    RadarRefreshOut,
)
from app.core.catalog import load_pipeline
from app.core.llm import BudgetExceeded
from app.db.enums import FindingStatus, MissionOrigin, MissionStatus, Verification
from app.db.models import (
    AskTurn,
    Capability,
    Entity,
    Mission,
    MissionFinding,
    MissionStep,
    RadarCandidate,
    Signal,
)

router = APIRouter(prefix="/api/missions", tags=["missions"])
Worker = Annotated[MissionWorker, Depends(get_worker)]


def _bad(e: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(e))


def _origin(session: Session, m: Mission) -> tuple[str, str | None]:
    ref = m.origin_ref or {}
    kind = m.origin_kind
    if kind == MissionOrigin.SIGNAL and (sig := session.get(Signal, ref.get("signal_id") or -1)):
        return f"Signal: {sig.title}"[:200], f"/signals?q={sig.id}"
    if kind == MissionOrigin.RADAR_CANDIDATE and (
        cand := session.get(RadarCandidate, ref.get("candidate_id") or -1)
    ):
        return f"Radar: {cand.name}", f"/radar/{cand.id}"
    if kind == MissionOrigin.CAPABILITY:
        e = session.get(Entity, ref.get("entity_id") or -1)
        c = session.get(Capability, ref.get("capability_id") or -1)
        if e and c:
            return f"Battlecard: {e.name} · {c.name}", f"/battlecards/{e.slug}"
    if kind == MissionOrigin.ASK_TURN and (t := session.get(AskTurn, ref.get("turn_id") or -1)):
        return f"Ask: {t.question}"[:200], f"/ask?c={t.conversation_id}"
    if (e := session.get(Entity, ref.get("entity_id") or -1)) is not None:
        return f"Battlecard: {e.name}", f"/battlecards/{e.slug}"
    return "New mission", None


def _scope_label(m: Mission) -> str:
    scope = m.scope or {}
    parts = list(scope.get("company_names") or [])
    if days := scope.get("days"):
        parts.append(f"last {days} days")
    return " · ".join(parts) or "No restriction"


def _counts(session: Session, ids: list[int]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {i: {"steps": 0, "new": 0, "approved": 0} for i in ids}
    if not ids:
        return out
    for mid, n in session.execute(
        select(MissionStep.mission_id, func.count())
        .where(MissionStep.mission_id.in_(ids), MissionStep.kind.in_(COUNTED_KINDS))
        .group_by(MissionStep.mission_id)
    ):
        out[mid]["steps"] = n
    for mid, status, n in session.execute(
        select(MissionFinding.mission_id, MissionFinding.status, func.count())
        .where(
            MissionFinding.mission_id.in_(ids),
            MissionFinding.status.in_((FindingStatus.NEW, FindingStatus.APPROVED)),
            MissionFinding.verification.in_((Verification.SUPPORTED, Verification.PARTIAL)),
        )
        .group_by(MissionFinding.mission_id, MissionFinding.status)
    ):
        out[mid][status.value] = n
    return out


def _mission_out(session: Session, m: Mission, counts: dict[str, int]) -> dict[str, Any]:
    label, link = _origin(session, m)
    scope = m.scope or {}
    return {
        "id": m.id, "title": m.title, "brief": m.brief, "status": m.status.value,
        "origin_kind": m.origin_kind.value, "origin_ref": m.origin_ref or {},
        "origin_label": label, "origin_link": link, "scope_label": _scope_label(m),
        "company_ids": list(scope.get("company_ids") or []), "days": scope.get("days"),
        "budget_usd": float(m.budget_usd), "cost_usd": float(m.cost_usd),
        "max_steps": m.max_steps, "steps_used": counts["steps"],
        "findings_new": counts["new"], "findings_approved": counts["approved"],
        "created_by": m.created_by, "created_at": m.created_at, "started_at": m.started_at,
        "finished_at": m.finished_at, "error": m.error,
    }  # fmt: skip


def _finding_out(f: MissionFinding) -> MissionFindingOut:
    src = f.source
    return MissionFindingOut(
        id=f.id,
        sub_question_no=f.sub_question_no,
        statement=f.statement,
        evidence_quote=f.evidence_quote,
        source=MissionSourceOut(
            id=src.id, kind=src.kind.value, url=src.url, title=src.title, signal_id=src.signal_id
        ),
        confidence=f.confidence.value,
        verification=f.verification.value,  # type: ignore[arg-type]
        verification_label=(
            "Partly verified" if f.verification == Verification.PARTIAL else "Verified"
        ),
        verification_notes=f.verification_notes,
        is_claim=f.is_claim,
        impact=f.impact,
        status=f.status.value,  # type: ignore[arg-type]
        signal_id=f.signal_id,
        reviewed_by=f.reviewed_by,
    )


def _get(session: Session, mission_id: int) -> Mission:
    m = session.get(Mission, mission_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No mission {mission_id}")
    return m


def _detail(session: Session, m: Mission) -> MissionDetailOut:
    counts = _counts(session, [m.id])[m.id]
    findings = list(
        session.scalars(
            select(MissionFinding)
            .where(MissionFinding.mission_id == m.id)
            .order_by(MissionFinding.sub_question_no, MissionFinding.id)
        )
    )
    shown = [
        f
        for f in findings
        if f.status != FindingStatus.DROPPED
        and f.verification in (Verification.SUPPORTED, Verification.PARTIAL)
    ]
    dropped = [f for f in findings if f not in shown]
    plan = None
    if m.plan and m.plan.get("sub_questions"):
        plan = MissionPlanOut(
            sub_questions=m.plan["sub_questions"],
            approved=bool(m.plan.get("approved")),
            approved_by=m.plan.get("approved_by"),
            edited_by_analyst=bool(m.plan.get("edited_by_analyst")),
        )
    report = None
    if m.report and "open_questions" in m.report:
        report = MissionReportOut(
            summary=m.report.get("summary") or [],
            open_questions=m.report.get("open_questions") or [],
            stopped_reason=m.report.get("stopped_reason"),
            generated=m.report.get("generated") or "fallback",
        )
    return MissionDetailOut(
        **_mission_out(session, m, counts),
        plan=plan,
        steps=[
            MissionStepOut(
                step_no=s.step_no,
                kind=s.kind.value,
                summary=s.summary,
                cost_usd=float(s.cost_usd),
                created_at=s.created_at,
                counted=s.kind in COUNTED_KINDS,
            )  # fmt: skip
            for s in session.scalars(
                select(MissionStep)
                .where(MissionStep.mission_id == m.id)
                .order_by(MissionStep.step_no)
            )
        ],
        findings=[_finding_out(f) for f in shown],
        dropped_count=len(dropped),
        dropped=[(f.dropped_reason or "dropped")[:200] for f in dropped],
        report=report,
        radar_candidate_id=m.radar_candidate_id,
        stop_requested=m.stop_requested,
    )


@router.get("/prefill", response_model=MissionPrefillOut)
def get_prefill(
    session: DbSession,
    signal: int | None = None,
    candidate: int | None = None,
    entity: int | None = None,
    capability: int | None = None,
    turn: int | None = None,
    entity_slug: str | None = None,
    capability_slug: str | None = None,
) -> MissionPrefillOut:
    """Form defaults for a mission started from a signal, a radar candidate, a battlecard
    (entity, optionally a capability; ids or slugs) or an Ask turn."""
    cfg = load_pipeline().missions
    if entity_slug:
        entity = session.scalar(select(Entity.id).where(Entity.slug == entity_slug))
        if entity is None:
            raise HTTPException(status_code=404, detail=f"No company {entity_slug}")
    if capability_slug:
        capability = session.scalar(select(Capability.id).where(Capability.slug == capability_slug))
        if capability is None:
            raise HTTPException(status_code=404, detail=f"No capability {capability_slug}")
    try:
        p = prefill(session, signal=signal, candidate=candidate, entity=entity,
                    capability=capability, turn=turn)  # fmt: skip
    except MissionInputError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return MissionPrefillOut(
        brief=p.brief, origin_kind=p.origin_kind.value, origin_ref=p.origin_ref,
        origin_label=p.origin_label, company_ids=p.company_ids, days=p.days,
        budget_usd=cfg.default_budget_usd, max_steps=cfg.default_max_steps,
        max_budget_usd=cfg.max_budget_usd, max_max_steps=cfg.max_max_steps,
    )  # fmt: skip


@router.post("", response_model=MissionDetailOut, status_code=201)
def post_mission(body: MissionCreateIn, session: DbSession, worker: Worker) -> MissionDetailOut:
    try:
        m = create_mission(
            session,
            brief=body.brief,
            created_by=body.analyst,
            origin_kind=MissionOrigin(body.origin_kind),
            origin_ref=body.origin_ref,
            company_ids=body.scope.company_ids,
            days=body.scope.days,
            budget_usd=body.budget_usd,
            max_steps=body.max_steps,
        )
    except MissionInputError as e:
        session.rollback()
        raise _bad(e) from e
    session.commit()
    worker.submit(m.id, "plan")
    return _detail(session, m)


@router.get("", response_model=MissionListOut)
def list_missions(
    session: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> MissionListOut:
    missions = list(session.scalars(select(Mission).order_by(Mission.id.desc()).limit(limit)))
    counts = _counts(session, [m.id for m in missions])
    return MissionListOut(
        items=[MissionOut(**_mission_out(session, m, counts[m.id])) for m in missions]
    )


@router.get("/{mission_id}", response_model=MissionDetailOut)
def get_mission(mission_id: int, session: DbSession) -> MissionDetailOut:
    return _detail(session, _get(session, mission_id))


@router.post("/{mission_id}/plan", response_model=MissionDetailOut)
def post_plan(
    mission_id: int, body: MissionPlanIn, session: DbSession, worker: Worker
) -> MissionDetailOut:
    m = _get(session, mission_id)
    try:
        approve_plan(
            session,
            m,
            [q.model_dump() for q in body.sub_questions] if body.sub_questions else None,
            body.analyst,
        )
    except MissionInputError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    session.commit()
    worker.submit(m.id, "research")
    return _detail(session, m)


@router.post("/{mission_id}/stop", response_model=MissionDetailOut)
def post_stop(mission_id: int, session: DbSession, now: Now) -> MissionDetailOut:
    m = _get(session, mission_id)
    try:
        stop_mission(session, m, now)
    except MissionInputError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    session.commit()
    return _detail(session, m)


def _finding(session: Session, finding_id: int) -> MissionFinding:
    f = session.get(MissionFinding, finding_id)
    if f is None or f.status == FindingStatus.DROPPED:
        raise HTTPException(status_code=404, detail=f"No finding {finding_id}")
    return f


@router.post("/findings/{finding_id}/approve", response_model=FindingReviewOut)
def post_approve(
    finding_id: int, body: FindingReviewIn, session: DbSession, now: Now, llm: Llm
) -> FindingReviewOut:
    f = _finding(session, finding_id)
    try:
        signal_id = approve_finding(session, f, body.analyst, now, llm.embed)
    except MissionInputError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    session.commit()
    return FindingReviewOut(finding=_finding_out(f), signal_id=signal_id)


@router.post("/findings/{finding_id}/reject", response_model=FindingReviewOut)
def post_reject(
    finding_id: int, body: FindingReviewIn, session: DbSession, now: Now
) -> FindingReviewOut:
    f = _finding(session, finding_id)
    try:
        reject_finding(session, f, body.analyst, now, body.reason, body.comment)
    except MissionInputError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    session.commit()
    return FindingReviewOut(finding=_finding_out(f), signal_id=None)


@router.post("/{mission_id}/refresh-radar", response_model=RadarRefreshOut)
def post_refresh_radar(mission_id: int, session: DbSession, now: Now, llm: Llm) -> RadarRefreshOut:
    m = _get(session, mission_id)
    if m.status in (MissionStatus.RUNNING, MissionStatus.PLANNING):
        raise HTTPException(status_code=409, detail="Wait until the mission has finished.")
    try:
        out = refresh_radar_profile(session, m, llm.complete, llm.embed, now)
    except MissionInputError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except BudgetExceeded as e:
        session.rollback()
        raise HTTPException(status_code=429, detail=str(e)) from e
    session.commit()
    return RadarRefreshOut(**out)
