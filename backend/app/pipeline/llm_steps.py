"""Shared plumbing for the LLM stages: work selection, parallel execution, meta updates.

Each signal is processed by a worker in a bounded thread pool and written in its own transaction.
A worker exception is recorded on the signal (meta.<stage>_error) and counted; it never stops the
run. BudgetExceeded stops the stage: queued work is cancelled and the stage reports `stopped`.
"""

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, or_, select, type_coerce, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker

from app.core.llm import BudgetExceeded
from app.db.models import Signal, Source
from app.pipeline.prompting import SignalInput

logger = logging.getLogger(__name__)

_MAX_ERROR_CHARS = 300


def canonical_or_unclustered() -> ColumnElement[bool]:
    """Signals the LLM stages process themselves; duplicates inherit from their canonical."""
    return or_(Signal.dedup_cluster_id.is_(None), Signal.dedup_cluster_id == Signal.id)


def meta_version_differs(stage: str, version: str) -> ColumnElement[bool]:
    return Signal.meta[stage]["prompt_version"].astext.is_distinct_from(version)


def merge_meta(parts: dict[str, Any]) -> Any:
    """SQL: meta || parts (top-level keys replaced, others kept). Safe under concurrent writers."""
    return Signal.meta.op("||")(type_coerce(parts, JSONB))


def load_input(session: Session, signal_id: int) -> SignalInput:
    row = session.execute(
        select(
            Signal.id,
            Signal.title,
            Signal.raw_text,
            Signal.published_at,
            Source.name,
            Source.kind,
        )
        .select_from(Signal)
        .outerjoin(Source, Source.id == Signal.source_id)
        .where(Signal.id == signal_id)
    ).one()
    return SignalInput(
        id=row.id,
        title=row.title,
        raw_text=row.raw_text,
        source=row.name or "research finding",
        source_kind=str(row.kind or "none"),
        published=row.published_at.date().isoformat() if row.published_at else "unknown",
    )


def record_error(
    factory: sessionmaker[Session], stage: str, signal_id: int, exc: Exception
) -> None:
    error = {
        "error": f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS],
        "at": datetime.now(UTC).isoformat(),
    }
    try:
        with factory.begin() as s:
            s.execute(
                update(Signal)
                .where(Signal.id == signal_id)
                .values(meta=merge_meta({f"{stage}_error": error}))
            )
    except Exception:  # noqa: BLE001 - best effort; the run counts the failure anyway
        logger.exception("could not record %s error on signal %s", stage, signal_id)


def clear_error(stage: str) -> Any:
    return Signal.meta.op("-")(f"{stage}_error")


def run_parallel(
    stage: str,
    ids: Sequence[int],
    worker: Callable[[int], str],
    *,
    factory: sessionmaker[Session],
    max_workers: int,
) -> dict[str, Any]:
    """Run worker(id) for every id; returns counts per worker outcome plus failed/stopped."""
    counts: dict[str, Any] = {"selected": len(ids), "failed": 0}
    if not ids:
        return counts
    stopped = False
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=stage) as pool:
        futures: dict[Future[str], int] = {pool.submit(worker, i): i for i in ids}
        for fut in as_completed(futures):
            signal_id = futures[fut]
            if fut.cancelled():
                continue
            try:
                outcome = fut.result()
            except BudgetExceeded as e:
                if not stopped:
                    stopped = True
                    counts["stopped"] = f"budget: {e}"[:_MAX_ERROR_CHARS]
                    for other in futures:
                        other.cancel()
                continue
            except Exception as e:  # noqa: BLE001 - one signal never stops the run
                logger.warning("%s failed for signal %s: %s", stage, signal_id, e)
                counts["failed"] += 1
                counts.setdefault("errors", []).append(
                    {"signal_id": signal_id, "error": f"{type(e).__name__}: {e}"[:200]}
                )
                record_error(factory, stage, signal_id, e)
                continue
            counts[outcome] = counts.get(outcome, 0) + 1
    if "errors" in counts:
        counts["errors"] = counts["errors"][:10]
    return counts
