"""Analyst review of signals (S14, D68): approve / correct / reject, the review queue, and the
AI vs Analyst scorecard.

No auth yet: the analyst name comes from the request body (the UI's "Your name" field).
Every action writes a feedback row. A correction changes the displayed fields; the model's own
values stay in meta.model_output (app.pipeline.analyst).
"""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import DbSession
from app.api.queries import reviewable, signals_query, to_outs, visible
from app.api.schemas import (
    FieldRate,
    FieldRates,
    ImpactRate,
    ReasonCount,
    ReviewProgress,
    ReviewQueueOut,
    ScorecardOut,
    ScorecardStats,
    ScorecardWeek,
    SignalApproveIn,
    SignalCorrectIn,
    SignalOut,
    SignalRejectIn,
)
from app.db.enums import (
    EntityReviewStatus,
    FeedbackAction,
    FeedbackTarget,
    ReasonCode,
    ReviewStatus,
)
from app.db.models import Entity, Feedback, Signal
from app.pipeline.analyst import FIELDS, NO_ENTITY, current_values, write_values
from app.pipeline.llm_steps import merge_meta

router = APIRouter(prefix="/api", tags=["review"])

REVIEW_ACTIONS = (FeedbackAction.APPROVE, FeedbackAction.REJECT, FeedbackAction.EDIT)


# ---------- actions ----------


def _get(session: Session, signal_id: int) -> Signal:
    sig = session.scalar(select(Signal).where(Signal.id == signal_id, reviewable()))
    if sig is None:
        raise HTTPException(404, "Signal not found")
    return sig


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _feedback(
    session: Session, sig: Signal, action: FeedbackAction, analyst: str, **kw: Any
) -> None:
    session.add(
        Feedback(
            target_type=FeedbackTarget.SIGNAL,
            target_id=sig.id,
            action=action,
            analyst=analyst.strip() or "analyst",
            **kw,
        )
    )


def _set_meta(session: Session, sig: Signal, parts: dict[str, Any]) -> None:
    session.execute(update(Signal).where(Signal.id == sig.id).values(meta=merge_meta(parts)))


def _out(session: Session, signal_id: int) -> SignalOut:
    session.expire_all()
    sig = session.scalars(signals_query().where(Signal.id == signal_id)).one()
    return to_outs(session, [sig])[0]


def _analyst(sig: Signal, by: str, action: str, **kw: Any) -> dict[str, Any]:
    old = (sig.meta or {}).get("analyst") or {}
    return {**old, "by": by.strip() or "analyst", "at": _now(), "action": action, **kw}


@router.post("/signals/{signal_id}/approve", response_model=SignalOut, responses={404: {}, 409: {}})
def approve(signal_id: int, session: DbSession, body: SignalApproveIn | None = None) -> SignalOut:
    body = body or SignalApproveIn()
    sig = _get(session, signal_id)
    if sig.status != ReviewStatus.NEW:
        raise HTTPException(409, f"Only new signals can be approved (this one is {sig.status})")
    analyst = _analyst(sig, body.analyst, "approve", comment=body.comment)
    analyst.setdefault("fields", {})
    sig.status = ReviewStatus.APPROVED
    _set_meta(session, sig, {"analyst": analyst})
    _feedback(session, sig, FeedbackAction.APPROVE, body.analyst, comment=body.comment)
    session.commit()
    return _out(session, signal_id)


@router.post("/signals/{signal_id}/reject", response_model=SignalOut, responses={404: {}, 409: {}})
def reject(signal_id: int, body: SignalRejectIn, session: DbSession) -> SignalOut:
    sig = _get(session, signal_id)
    if sig.status == ReviewStatus.REJECTED:
        raise HTTPException(409, "This signal is already rejected")
    reason = ReasonCode(body.reason_code)
    analyst = _analyst(sig, body.analyst, "reject", reason_code=reason.value, comment=body.comment)
    analyst.setdefault("fields", {})
    sig.status = ReviewStatus.REJECTED
    _set_meta(session, sig, {"analyst": analyst})
    _feedback(
        session, sig, FeedbackAction.REJECT, body.analyst, reason_code=reason, comment=body.comment
    )
    session.commit()
    return _out(session, signal_id)


@router.post(
    "/signals/{signal_id}/correct",
    response_model=SignalOut,
    responses={404: {}, 409: {}, 422: {}},
)
def correct(signal_id: int, body: SignalCorrectIn, session: DbSession) -> SignalOut:
    sig = _get(session, signal_id)
    if sig.status == ReviewStatus.REJECTED:
        raise HTTPException(409, "A rejected signal cannot be corrected")
    shown = current_values(session, sig)
    asked = {f: getattr(body, f) for f in FIELDS if getattr(body, f) is not None}
    if "category" in asked:
        asked["category"] = asked["category"].value
    if "primary_entity" in asked and asked["primary_entity"] != NO_ENTITY:
        known = session.scalar(
            select(Entity.id).where(
                Entity.slug == asked["primary_entity"],
                Entity.review_status == EntityReviewStatus.APPROVED,
            )
        )
        if known is None:
            raise HTTPException(422, f"Unknown entity {asked['primary_entity']!r}")
    changes = {f: v for f, v in asked.items() if v != shown[f]}
    if not changes:
        raise HTTPException(422, "Nothing to correct: every field equals the shown value")

    meta = sig.meta or {}
    # The model's own values, saved once before the first correction (re-enrichment refreshes them).
    model = meta.get("model_output") or {
        **shown,
        "triage_prompt_version": (meta.get("triage") or {}).get("prompt_version"),
        "enrich_prompt_version": sig.enrich_prompt_version,
    }
    fields = dict(((meta.get("analyst") or {}).get("fields")) or {})
    for f, to in changes.items():
        if to == model.get(f):
            fields.pop(f, None)  # back to what the model said: no longer a correction
        else:
            fields[f] = {"from": model.get(f), "to": to}
    reason = ReasonCode(body.reason_code)
    analyst = _analyst(
        sig, body.analyst, "edit", reason_code=reason.value, comment=body.comment, fields=fields
    )
    write_values(session, sig.id, changes)
    rejected = changes.get("relevant") is False
    sig.status = ReviewStatus.REJECTED if rejected else ReviewStatus.APPROVED
    _set_meta(session, sig, {"analyst": analyst, "model_output": model})
    _feedback(
        session,
        sig,
        FeedbackAction.EDIT,
        body.analyst,
        reason_code=reason,
        comment=body.comment,
        correction={f: {"from": shown[f], "to": v} for f, v in changes.items()},
    )
    session.commit()
    return _out(session, signal_id)


# ---------- review queue ----------


@router.get("/review", response_model=ReviewQueueOut)
def review_queue(
    session: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 100
) -> ReviewQueueOut:
    """Signals waiting for review (status new), most important first: impact × confidence."""
    pending = [visible(), Signal.relevant.is_(True), Signal.status == ReviewStatus.NEW]
    score = func.coalesce(Signal.impact, 0) * func.coalesce(Signal.confidence, 0)
    rows = session.scalars(
        signals_query().where(*pending).order_by(score.desc(), Signal.id.desc()).limit(limit)
    ).all()
    # Reviewed: the same population after an analyst acted (rejected ones included).
    population = [
        reviewable(),
        or_(Signal.relevant.is_(True), Signal.status == ReviewStatus.REJECTED),
    ]
    count = select(func.count()).select_from(Signal)
    waiting = session.scalar(count.where(*pending)) or 0
    reviewed = session.scalar(count.where(*population, Signal.status != ReviewStatus.NEW)) or 0
    return ReviewQueueOut(
        items=to_outs(session, rows),
        progress=ReviewProgress(reviewed=reviewed, total=reviewed + waiting),
    )


# ---------- AI vs Analyst scorecard ----------


@dataclass
class Reviewed:
    """One reviewed signal: its outcome now and when it was last reviewed."""

    outcome: str  # approved / corrected / rejected
    fields: dict[str, dict[str, Any]]
    reason: str | None  # reason of the rejection (relevance errors)
    at: datetime


@dataclass
class _Acc:
    items: list[Reviewed] = field(default_factory=list)
    reasons: Counter[str] = field(default_factory=Counter)


def _pct(n: int, d: int) -> float:
    return round(100 * n / d, 1) if d else 0.0


def _rate(n: int, d: int) -> FieldRate:
    return FieldRate(corrected=n, rate=round(n / d, 3) if d else 0.0)


def stats(items: list[Reviewed], reasons: Counter[str], top: int = 5) -> ScorecardStats:
    n = len(items)
    by = Counter(i.outcome for i in items)

    def changed(f: str) -> list[Reviewed]:
        return [i for i in items if f in i.fields]

    impact = changed("impact")
    deltas = [
        i.fields["impact"]["to"] - i.fields["impact"]["from"]
        for i in impact
        if isinstance(i.fields["impact"].get("from"), int)
    ]
    relevance = [
        i
        for i in items
        if "relevant" in i.fields or (i.outcome == "rejected" and i.reason == "not_relevant")
    ]
    return ScorecardStats(
        reviewed=n,
        approved=by["approved"],
        corrected=by["corrected"],
        rejected=by["rejected"],
        approved_pct=_pct(by["approved"], n),
        corrected_pct=_pct(by["corrected"], n),
        rejected_pct=_pct(by["rejected"], n),
        fields=FieldRates(
            category=_rate(len(changed("category")), n),
            impact=ImpactRate(
                **_rate(len(impact), n).model_dump(),
                mean_delta=round(sum(deltas) / len(deltas), 2) if deltas else None,
            ),
            relevance=_rate(len(relevance), n),
            entity=_rate(len(changed("primary_entity")), n),
        ),
        reasons=[
            ReasonCount(code=ReasonCode(c), count=k)
            for c, k in sorted(reasons.items(), key=lambda x: (-x[1], x[0]))[:top]
        ],
    )


def week_start(at: datetime) -> str:
    day = at.astimezone(UTC).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def scorecard_from(
    items: Iterable[Reviewed], reason_rows: Iterable[tuple[str, datetime]]
) -> ScorecardOut:
    overall = _Acc()
    weeks: dict[str, _Acc] = defaultdict(_Acc)
    for i in items:
        overall.items.append(i)
        weeks[week_start(i.at)].items.append(i)
    for code, at in reason_rows:
        overall.reasons[code] += 1
        weeks[week_start(at)].reasons[code] += 1
    return ScorecardOut(
        overall=stats(overall.items, overall.reasons),
        weeks=[
            ScorecardWeek(week_start=w, **stats(acc.items, acc.reasons).model_dump())
            for w, acc in sorted(weeks.items(), reverse=True)
        ],
    )


def reviewed_signals(session: Session) -> list[Reviewed]:
    """Every signal with a review action, classified by its current state."""
    last = (
        select(Feedback.target_id, func.max(Feedback.created_at).label("at"))
        .where(Feedback.target_type == FeedbackTarget.SIGNAL, Feedback.action.in_(REVIEW_ACTIONS))
        .group_by(Feedback.target_id)
        .subquery()
    )
    rows = session.execute(
        select(Signal.status, Signal.meta, last.c.at).join(last, last.c.target_id == Signal.id)
    ).all()
    out = []
    for status, meta, at in rows:
        analyst = (meta or {}).get("analyst") or {}
        fields = analyst.get("fields") or {}
        if status == ReviewStatus.REJECTED:
            outcome = "rejected"
        elif fields:
            outcome = "corrected"
        elif status == ReviewStatus.APPROVED:
            outcome = "approved"
        else:
            continue  # acted on, then reset (text changed): not reviewed now
        reason = analyst.get("reason_code") if status == ReviewStatus.REJECTED else None
        out.append(Reviewed(outcome, fields, reason, at))
    return out


@router.get("/feedback/scorecard", response_model=ScorecardOut)
def scorecard(session: DbSession) -> ScorecardOut:
    reasons = session.execute(
        select(Feedback.reason_code, Feedback.created_at).where(
            Feedback.target_type == FeedbackTarget.SIGNAL,
            Feedback.action.in_(REVIEW_ACTIONS),
            Feedback.reason_code.is_not(None),
        )
    ).all()
    return scorecard_from(reviewed_signals(session), [(str(c), at) for c, at in reasons])
