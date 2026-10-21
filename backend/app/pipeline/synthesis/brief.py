"""Brief node: one daily_brief report per day – headline + 2-3 sentence overview, every sentence
citing the signals it relies on (synthesis model), checked in code and by a second model.

- Input: the top `max_signals` usable signals of the last `window_days` (scope.ranked, the same
  ranking as the Brief's Top 5). None → skipped, no call.
- Idempotent per UTC day: a daily_brief whose period_end falls on today → skipped (--force brief
  regenerates).
- Checks: citations (any id outside the input drops the sentence), then the verify model
  (partial / unsupported drops it). Headline dropped or fewer than 2 overview sentences left → no
  report is stored (counts.rejected); the API keeps its fallback title.
"""

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import cast, func, select
from sqlalchemy.orm import Session
from sqlalchemy.types import Date

from app.core.catalog import load_llm_routing
from app.core.llm import load_prompt
from app.db.enums import ReportKind
from app.db.models import Report
from app.pipeline.context import RunContext
from app.pipeline.scope import ranked
from app.pipeline.synthesis.check import verify_claims
from app.pipeline.synthesis.citations import Claim, check_citations
from app.pipeline.synthesis.inputs import facts, signals_block
from app.pipeline.synthesis.schemas import DailyBrief

logger = logging.getLogger(__name__)

PROMPT = "daily_brief"
MIN_OVERVIEW = 2


def brief_exists_today(session: Session, ctx: RunContext) -> bool:
    day = ctx.now.date()
    return (
        session.scalar(
            select(func.count())
            .select_from(Report)
            .where(
                Report.kind == ReportKind.DAILY_BRIEF,
                cast(func.timezone("UTC", Report.period_end), Date) == day,
            )
        )
        or 0
    ) > 0


def brief_step(session: Session, ctx: RunContext) -> dict[str, Any]:
    complete, _ = ctx.require_llm()
    cfg = ctx.config.synthesis.brief
    if "brief" not in ctx.force and brief_exists_today(session, ctx):
        return {"skipped": "brief exists for today"}
    since = ctx.now - timedelta(days=cfg.window_days)
    signals = session.scalars(ranked(since, ctx.now).limit(cfg.max_signals)).all()
    if not signals:
        return {"skipped": "no relevant signals"}
    items = facts(session, signals)
    by_id = {f.id: f for f in items}

    prompt = load_prompt(PROMPT)
    messages = prompt.messages(
        today=ctx.now.date().isoformat(),
        window_days=cfg.window_days,
        count=len(items),
        **signals_block(items),
    )
    out: DailyBrief = complete("synthesis", PROMPT, messages, DailyBrief, prompt_version=prompt.id)

    claims = [Claim("headline", out.headline.text, tuple(out.headline.signal_ids))] + [
        Claim(f"overview.{i}", s.text, tuple(s.signal_ids)) for i, s in enumerate(out.overview, 1)
    ]
    cited = check_citations(session, claims, by_id)
    dropped = list(cited.dropped)
    counts: dict[str, Any] = {"input_signals": len(items), "proposed": len(claims)}
    check_version = None
    if _complete_enough(cited.kept):
        checked, check_version = verify_claims(
            cited.kept, by_id, complete, kind="daily brief", subject="the last days' news"
        )
        kept, dropped = checked.kept, dropped + checked.dropped
    else:
        kept = cited.kept  # not enough left: skip the paid check, nothing is stored anyway
    counts |= {"kept": len(kept), "dropped": len(dropped)}

    if not _complete_enough(kept):
        reasons = [f"{d['key']}: {d['reason']}" for d in dropped]
        logger.warning("brief rejected: %s", reasons)
        return {**counts, "rejected": 1, "reasons": reasons[:10]}

    headline = next(c for c in kept if c.key == "headline")
    overview = [c for c in kept if c.key.startswith("overview.")]
    cited_ids = sorted({i for c in kept for i in c.signal_ids})
    session.add(
        Report(
            kind=ReportKind.DAILY_BRIEF,
            period_start=since,
            period_end=ctx.now,
            signal_ids=cited_ids,
            run_id=ctx.run_id,
            model=load_llm_routing().tasks.synthesis.model,
            content={
                "headline": headline.text,
                "headline_signal_ids": list(headline.signal_ids),
                "overview": " ".join(c.text for c in overview),
                "overview_sentences": [
                    {"text": c.text, "signal_ids": list(c.signal_ids)} for c in overview
                ],
                "input_ids": [f.id for f in items],
                "dropped": dropped,
                "prompt_version": prompt.id,
                "check_prompt_version": check_version,
            },
        )
    )
    session.flush()
    return {**counts, "stored": 1}


def _complete_enough(claims: list[Claim]) -> bool:
    keys = [c.key for c in claims]
    return "headline" in keys and sum(k.startswith("overview.") for k in keys) >= MIN_OVERVIEW
