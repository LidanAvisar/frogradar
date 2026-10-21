"""Gold labels from analyst reviews (D70): every signal an analyst approved, corrected or
rejected as not relevant becomes a labeled eval item in the silver-label format
(eval/gold_labels.csv), so the eval set grows from daily use.
`make eval ARGS="--labels eval/gold_labels.csv"` scores the pipeline against them.

Label values: the analyst's corrected value where there is one, else the model's value (an
approval says the model was right). Rejections for other reasons (duplicate, outdated, inaccurate
summary) say nothing about relevance or category and are left out.
No raw text or excerpts are written (README "raw_text").
"""

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.enums import FeedbackAction, FeedbackTarget, ReviewStatus
from app.db.models import Feedback, Signal
from app.eval.files import NONE_ENTITY, LabelRow, eval_dir, write_labels
from app.pipeline.analyst import current_values
from app.pipeline.scope import in_scope

RELEVANCE_REJECTIONS = (None, "not_relevant", "other")


def gold_path() -> Path:
    return eval_dir() / "gold_labels.csv"


def gold_rows(session: Session) -> list[LabelRow]:
    reviewed = (
        select(Feedback.target_id)
        .where(
            Feedback.target_type == FeedbackTarget.SIGNAL,
            Feedback.action.in_(
                (FeedbackAction.APPROVE, FeedbackAction.REJECT, FeedbackAction.EDIT)
            ),
        )
        .distinct()
    )
    signals = session.scalars(
        select(Signal)
        .options(selectinload(Signal.source))
        .where(Signal.id.in_(reviewed), Signal.status != ReviewStatus.NEW)
        .order_by(Signal.id)
    ).all()
    scoped = set(session.scalars(select(Signal.id).where(Signal.id.in_(reviewed), in_scope())))
    rows = []
    for sig in signals:
        row = _row(session, sig, sig.id in scoped)
        if row is not None:
            rows.append(row)
    return rows


def _row(session: Session, sig: Signal, scoped: bool) -> LabelRow | None:
    analyst: dict[str, Any] = (sig.meta or {}).get("analyst") or {}
    reason = analyst.get("reason_code")
    shown = current_values(session, sig)  # the displayed values = analyst-corrected where corrected
    rejected = sig.status == ReviewStatus.REJECTED
    if rejected and "relevant" not in (analyst.get("fields") or {}):
        if reason not in RELEVANCE_REJECTIONS:
            return None
        shown["relevant"] = False
    action = "edit" if analyst.get("fields") else analyst.get("action") or str(sig.status)
    relevant = bool(shown["relevant"])
    why = "; ".join(p for p in (reason, analyst.get("comment")) if p)
    return LabelRow(
        id=sig.id,
        title=sig.title,
        source=sig.source.name if sig.source else "research finding",
        date=sig.published_at.date().isoformat() if sig.published_at else "",
        url=sig.url,
        in_scope="y" if scoped else "n",
        stratum=f"gold:{action}",
        relevant="y" if relevant else "n",
        category=(shown["category"] or "") if relevant else "",
        impact=str(shown["impact"] or "") if relevant else "",
        primary_entity=(shown["primary_entity"] or NONE_ENTITY) if relevant else "",
        label_source=f"analyst:{analyst.get('by') or 'analyst'} {action}",
        label_reason=why,
    )


def export(session: Session, path: Path | None = None) -> tuple[Path, list[LabelRow]]:
    rows = gold_rows(session)
    out = path or gold_path()
    write_labels(rows, out)
    return out, rows
