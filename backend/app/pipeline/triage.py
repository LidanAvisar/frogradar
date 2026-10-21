"""Triage: a cheap relevance gate (bulk model) per canonical signal, before paying for enrichment.

Selects in-scope canonical (or not yet clustered) signals that were never triaged, were triaged
with another prompt version, or all of them with --force triage. Irrelevant signals are kept with
relevant=false (and skip enrichment); the reason and segments go to meta.triage.
Duplicates inherit their canonical's decision.
"""

import logging
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, aliased

from app.core.llm import load_prompt
from app.db.models import Signal
from app.pipeline.context import CompleteFn, RunContext
from app.pipeline.llm_steps import (
    canonical_or_unclustered,
    load_input,
    merge_meta,
    meta_version_differs,
    run_parallel,
)
from app.pipeline.prompting import Catalog, SignalInput, triage_messages
from app.pipeline.schemas import TriageResult
from app.pipeline.scope import in_scope

logger = logging.getLogger(__name__)


def triage_one(
    sig: SignalInput, catalog: Catalog, max_chars: int, complete: CompleteFn
) -> TriageResult:
    """One triage decision (no DB writes). Shared by the stage and the eval runner (app.eval)."""
    prompt, messages = triage_messages(sig, catalog, max_chars)
    return complete("bulk", "triage", messages, TriageResult, prompt_version=prompt.id)


def triage_signals(session: Session, ctx: RunContext) -> dict[str, Any]:
    complete, factory = ctx.require_llm()
    version = load_prompt("triage").id
    cfg = ctx.config.triage
    todo = [in_scope(), canonical_or_unclustered()]
    if "triage" not in ctx.force:
        todo.append(or_(Signal.relevant.is_(None), meta_version_differs("triage", version)))
    ids = session.scalars(select(Signal.id).where(*todo).order_by(Signal.id)).all()
    catalog = Catalog.load(session)

    def worker(signal_id: int) -> str:
        with factory() as s:
            sig = load_input(s, signal_id)
        result = triage_one(sig, catalog, cfg.max_chars, complete)
        info = {
            "reason": result.reason,
            "segments": [str(x) for x in result.segments],
            "prompt_version": version,
        }
        with factory.begin() as s:
            s.execute(
                update(Signal)
                .where(Signal.id == signal_id)
                .values(
                    relevant=result.relevant,
                    meta=merge_meta({"triage": info}).op("-")("triage_error"),
                )
            )
        return "relevant" if result.relevant else "irrelevant"

    counts = run_parallel(
        "triage", ids, worker, factory=factory, max_workers=ctx.config.llm.max_workers
    )
    counts["inherited"] = inherit_triage(session)
    logger.info("triage: %s", counts)
    return counts


def inherit_triage(session: Session) -> int:
    """Duplicates take their canonical's relevance decision. Returns rows changed."""
    canonical = aliased(Signal)
    rows = session.execute(
        select(Signal.id, canonical.id, canonical.relevant, canonical.meta["triage"])
        .join(canonical, canonical.id == Signal.dedup_cluster_id)
        .where(
            in_scope(),
            Signal.id != canonical.id,
            canonical.relevant.is_not(None),
            or_(
                Signal.relevant.is_distinct_from(canonical.relevant),
                Signal.meta["triage"]["prompt_version"].astext.is_distinct_from(
                    canonical.meta["triage"]["prompt_version"].astext
                ),
            ),
        )
    ).all()
    for dup_id, canonical_id, relevant, triage in rows:
        session.execute(
            update(Signal)
            .where(Signal.id == dup_id)
            .values(
                relevant=relevant,
                meta=merge_meta({"triage": {**(triage or {}), "inherited_from": canonical_id}}),
            )
        )
    session.flush()
    return len(rows)
