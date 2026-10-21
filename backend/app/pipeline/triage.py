"""Triage: a cheap relevance gate (bulk model) per canonical signal, before paying for enrichment.

Selects in-scope canonical (or not yet clustered) signals that were never triaged, were triaged
with another prompt version, or all of them with --force triage. Irrelevant signals are kept with
relevant=false (and skip enrichment); the reason and segments go to meta.triage.
Duplicates inherit their canonical's decision.
Discovery items (meta.kind = discovery: HN, GitHub search, CNCF) use the stricter
triage_discovery prompt: relevant only if the item's own product operates in JFrog's segments.
"""

import logging
from typing import Any

from sqlalchemy import case, or_, select, update
from sqlalchemy.orm import Session, aliased

from app.core.llm import load_prompt
from app.db.models import Signal
from app.pipeline.context import CompleteFn, RunContext
from app.pipeline.llm_steps import (
    canonical_or_unclustered,
    load_input,
    merge_meta,
    run_parallel,
)
from app.pipeline.prompting import Catalog, SignalInput, triage_messages
from app.pipeline.schemas import TriageResult
from app.pipeline.scope import discovery, in_scope

logger = logging.getLogger(__name__)


DISCOVERY_PROMPT = "triage_discovery"


def triage_one(
    sig: SignalInput,
    catalog: Catalog,
    max_chars: int,
    complete: CompleteFn,
    *,
    prompt_name: str = "triage",
) -> TriageResult:
    """One triage decision (no DB writes). Shared by the stage and the eval runner (app.eval)."""
    prompt, messages = triage_messages(sig, catalog, max_chars, prompt_name)
    return complete("bulk", prompt_name, messages, TriageResult, prompt_version=prompt.id)


def triage_signals(session: Session, ctx: RunContext) -> dict[str, Any]:
    complete, factory = ctx.require_llm()
    version = load_prompt("triage").id
    disc_version = load_prompt(DISCOVERY_PROMPT).id
    cfg = ctx.config.triage
    todo = [in_scope(), canonical_or_unclustered()]
    if "triage" not in ctx.force:
        # Each kind of signal is compared with its own prompt's current version.
        current = case((discovery(), disc_version), else_=version)
        todo.append(
            or_(
                Signal.relevant.is_(None),
                Signal.meta["triage"]["prompt_version"].astext.is_distinct_from(current),
            )
        )
    ids = session.scalars(select(Signal.id).where(*todo).order_by(Signal.id)).all()
    catalog = Catalog.load(session)

    def worker(signal_id: int) -> str:
        with factory() as s:
            sig = load_input(s, signal_id)
            disc = s.scalar(select(discovery()).where(Signal.id == signal_id))
        name = DISCOVERY_PROMPT if disc else "triage"
        result = triage_one(sig, catalog, cfg.max_chars, complete, prompt_name=name)
        info = {
            "reason": result.reason,
            "segments": [str(x) for x in result.segments],
            "prompt_version": disc_version if disc else version,
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
