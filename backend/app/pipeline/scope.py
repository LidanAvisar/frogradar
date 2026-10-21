"""Which signals later pipeline stages (dedup, enrichment, verification) work on.

A signal is in scope if its source is active, or if it has no source (research-mission findings).
Signals from inactive sources stay in the DB (history, citations) but are not processed further.
Every stage selects its work through these helpers, so "in scope" has one definition.

Two other selections exist for evaluation only (app.eval), never for the daily pipeline:
- full_catalog(): every signal, any source (dedup calibration, eval-set sampling).
- eval_set(ids): exactly the labeled signals of the eval set (eval/labels.csv).
"""

from collections.abc import Iterable

from sqlalchemy import ColumnElement, Select, exists, func, or_, select, true
from sqlalchemy.orm import Session

from app.db.models import Signal, Source


def in_scope() -> ColumnElement[bool]:
    """WHERE clause for signals the pipeline should process."""
    # correlate_except: stays a real subquery even when the caller's query joins `sources`.
    active_source = (
        exists().where(Source.id == Signal.source_id, Source.is_active).correlate_except(Source)
    )
    return or_(Signal.source_id.is_(None), active_source)


def in_scope_signals() -> Select[tuple[Signal]]:
    """`select(Signal)` limited to in-scope signals; stages add their own filters/order."""
    return select(Signal).where(in_scope())


def count_in_scope(session: Session) -> tuple[int, int]:
    """(in scope, total) signal counts."""
    total = session.scalar(select(func.count()).select_from(Signal)) or 0
    scoped = session.scalar(select(func.count()).select_from(Signal).where(in_scope())) or 0
    return scoped, total


def full_catalog() -> ColumnElement[bool]:
    """WHERE clause for every signal, active source or not (evaluation and calibration only)."""
    return true()


def eval_set(ids: Iterable[int]) -> ColumnElement[bool]:
    """WHERE clause for the labeled eval-set signals, in scope or not (evaluation only)."""
    return Signal.id.in_(sorted(set(ids)))
