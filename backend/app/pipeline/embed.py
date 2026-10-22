"""Embed in-scope signals that have no embedding, or one from a different model.

Input = title + the first `embedding.max_chars` characters of raw_text (config/pipeline.yaml).
The gateway caches each text by hash, so re-running after a failure costs nothing extra.
"""

import logging
from typing import Any

from sqlalchemy import ColumnElement, or_, select, update
from sqlalchemy.orm import Session

from app.db.models import Signal
from app.pipeline.context import RunContext
from app.pipeline.scope import in_scope, not_stripped

logger = logging.getLogger(__name__)


def embedding_input(title: str, raw_text: str, max_chars: int) -> str:
    return f"{title.strip()}\n\n{raw_text[:max_chars].strip()}".strip()


def embed_signals(
    session: Session, ctx: RunContext, where: ColumnElement[bool] | None = None
) -> dict[str, Any]:
    """where: which signals (app.pipeline.scope); default in_scope(). The eval's dedup
    calibration passes scope.full_catalog()."""
    cfg = ctx.config.embedding
    rows = session.execute(
        select(Signal.id, Signal.title, Signal.raw_text, Signal.embedding_model)
        .where(
            in_scope() if where is None else where,
            not_stripped(),
            or_(Signal.embedding.is_(None), Signal.embedding_model != ctx.embedding_model),
        )
        .order_by(Signal.id)
    ).all()
    counts = {"candidates": len(rows), "embedded": 0, "re_embedded": 0, "batches": 0}

    for i in range(0, len(rows), cfg.batch_size):
        batch = rows[i : i + cfg.batch_size]
        texts = [embedding_input(r.title, r.raw_text, cfg.max_chars) for r in batch]
        result = ctx.embed(texts)
        if result.model != ctx.embedding_model:
            raise RuntimeError(
                f"embed() used {result.model}, expected {ctx.embedding_model} (routing changed?)"
            )
        for row, vector in zip(batch, result.vectors, strict=True):
            session.execute(
                update(Signal)
                .where(Signal.id == row.id)
                .values(embedding=vector, embedding_model=result.model)
            )
            if row.embedding_model is not None:
                # Vectors from different models aren't comparable: cluster this signal again.
                counts["re_embedded"] += 1
                _reset_dedup(session, row.id)
        counts["embedded"] += len(batch)
        counts["batches"] += 1
        session.flush()

    logger.info("embed: %s", counts)
    return counts


def _reset_dedup(session: Session, signal_id: int) -> None:
    """Un-cluster a signal; if it was a canonical, its whole cluster is clustered again."""
    session.execute(
        update(Signal)
        .where(or_(Signal.id == signal_id, Signal.dedup_cluster_id == signal_id))
        .values(dedup_cluster_id=None)
    )
