"""Verify: a model from a different provider checks each enriched signal against its source.

Selects enriched in-scope canonical signals whose verification is pending, or that were verified
with another prompt version (or all with --force verify). The verifier sees the same truncated
source text the writer saw, plus summary, why_jfrog_cares and the evidence quote.
verdict → signals.verification; notes (+ unsupported claims) → verification_notes.
"""

import logging
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.llm import load_prompt
from app.db.enums import Verification
from app.db.models import Signal
from app.pipeline.context import RunContext
from app.pipeline.enrich import inherit_from_canonicals
from app.pipeline.llm_steps import (
    canonical_or_unclustered,
    load_input,
    merge_meta,
    meta_version_differs,
    run_parallel,
)
from app.pipeline.prompting import verify_messages
from app.pipeline.schemas import VerifyResult
from app.pipeline.scope import in_scope

logger = logging.getLogger(__name__)


def verification_notes(result: VerifyResult) -> str:
    notes = result.notes.strip()
    if result.unsupported_claims:
        notes += " Unsupported: " + "; ".join(c.strip() for c in result.unsupported_claims)
    return notes


def verify_signals(session: Session, ctx: RunContext) -> dict[str, Any]:
    complete, factory = ctx.require_llm()
    version = load_prompt("verify_signal").id
    cfg = ctx.config.verify
    todo = [
        in_scope(),
        canonical_or_unclustered(),
        Signal.relevant.is_(True),
        Signal.enriched_at.is_not(None),
    ]
    if "verify" not in ctx.force:
        todo.append(
            or_(
                Signal.verification == Verification.PENDING,
                meta_version_differs("verify", version),
            )
        )
    ids = session.scalars(select(Signal.id).where(*todo).order_by(Signal.id)).all()

    def worker(signal_id: int) -> str:
        with factory() as s:
            sig = load_input(s, signal_id)
            summary, why, quote = s.execute(
                select(Signal.summary, Signal.why_jfrog_cares, Signal.evidence_quote).where(
                    Signal.id == signal_id
                )
            ).one()
        prompt, messages = verify_messages(sig, summary, why, quote or "", cfg.max_chars)
        result = complete(
            "verify", "verify_signal", messages, VerifyResult, prompt_version=prompt.id
        )
        info = {"prompt_version": prompt.id, "unsupported_claims": result.unsupported_claims}
        with factory.begin() as s:
            s.execute(
                update(Signal)
                .where(Signal.id == signal_id)
                .values(
                    verification=Verification(result.verdict),
                    verification_notes=verification_notes(result),
                    confidence=None,
                    meta=merge_meta({"verify": info}).op("-")("verify_error"),
                )
            )
        return result.verdict

    counts = run_parallel(
        "verify", ids, worker, factory=factory, max_workers=ctx.config.llm.max_workers
    )
    counts["inherited"] = inherit_from_canonicals(session)
    logger.info("verify: %s", counts)
    return counts
