"""Which signals later pipeline stages (dedup, enrichment, verification) work on.

A signal is in scope if its source is active, or if it has no source (research-mission findings).
Signals from inactive sources stay in the DB (history, citations) but are not processed further.
Every stage selects its work through these helpers, so "in scope" has one definition.
"""

from sqlalchemy import ColumnElement, Select, exists, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Signal, Source


def in_scope() -> ColumnElement[bool]:
    """WHERE clause for signals the pipeline should process."""
    active_source = exists().where(Source.id == Signal.source_id, Source.is_active)
    return or_(Signal.source_id.is_(None), active_source)


def in_scope_signals() -> Select[tuple[Signal]]:
    """`select(Signal)` limited to in-scope signals; stages add their own filters/order."""
    return select(Signal).where(in_scope())


def count_in_scope(session: Session) -> tuple[int, int]:
    """(in scope, total) signal counts."""
    total = session.scalar(select(func.count()).select_from(Signal)) or 0
    scoped = session.scalar(select(func.count()).select_from(Signal).where(in_scope())) or 0
    return scoped, total
