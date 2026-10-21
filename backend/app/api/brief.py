"""GET /api/brief – Morning Brief: top developments, what's new, pricing watch, pipeline."""

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, Now
from app.api.prices import price_changes
from app.api.queries import (
    canonical,
    citation_refs,
    entity_refs,
    event_time,
    is_page_diff,
    ranked,
    signals_query,
    to_outs,
    visible,
)
from app.api.schemas import (
    BriefOut,
    BriefWindow,
    CitationRef,
    CitedSentenceOut,
    LastRun,
    PipelineInfo,
    PriceChange,
    PricingItem,
    RankedSignalOut,
    SignalOut,
    SinceYesterdayItem,
)
from app.db.enums import FetchMethod, ReportKind, Verification
from app.db.models import PipelineRun, Report, Signal, Source
from app.pipeline.scope import in_scope
from app.pipeline.synthesis.inputs import display_title

router = APIRouter(prefix="/api", tags=["brief"])

SINCE_YESTERDAY_MAX = 10
PRICING_WATCH_DAYS = 90
PRICING_WATCH_MAX = 4
HIGH_IMPACT = 4
FEED_METHODS = (FetchMethod.RSS, FetchMethod.GITHUB, FetchMethod.WEB)


@router.get("/brief", response_model=BriefOut)
def get_brief(
    session: DbSession,
    now: Now,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> BriefOut:
    since = now - timedelta(days=days)

    # Ranked: relevant, verified (supported or partial), impact × confidence (scope.ranked).
    top_rows = session.scalars(
        ranked(since, now).options(selectinload(Signal.source)).limit(limit)
    ).all()
    top = [
        RankedSignalOut(rank=i, **out.model_dump())
        for i, out in enumerate(to_outs(session, top_rows), start=1)
    ]

    # New = published in the last 24h. Backfilled old articles fetched today don't count;
    # fetched_at is used only when the source gave no publish date.
    is_new = (
        visible(),
        Signal.relevant.is_(True),
        Signal.verification != Verification.UNSUPPORTED,
        event_time() >= now - timedelta(hours=24),
        event_time() <= now,
    )
    new_total, new_high = session.execute(
        select(func.count(), func.count().filter(Signal.impact >= HIGH_IMPACT)).where(*is_new)
    ).one()
    # The list leaves out what the Top 5 already shows; the totals above count everything new.
    top_ids = [t.id for t in top]
    new_rows = session.scalars(
        select(Signal)
        .where(*is_new, Signal.id.not_in(top_ids))
        .order_by(Signal.impact.desc().nulls_last(), event_time().desc(), Signal.id.desc())
        .limit(SINCE_YESTERDAY_MAX)
    ).all()
    refs = entity_refs(session, [s.id for s in new_rows])
    since_yesterday = [
        SinceYesterdayItem(
            id=s.id,
            title=display_title(s),
            url=s.url,
            impact=s.impact,
            category=s.category,
            entities=refs.get(s.id, []),
            published_at=s.published_at,
        )  # fmt: skip
        for s in new_rows
    ]

    filtered = session.scalar(
        select(func.count())
        .select_from(Signal)
        .where(in_scope(), canonical(), Signal.relevant.is_(False), event_time() >= since)
    )

    return BriefOut(
        generated_at=now,
        window=BriefWindow(days=days, since=since),
        **_headline(session),
        top=top,
        since_yesterday=since_yesterday,
        since_yesterday_total=new_total,
        since_yesterday_high_impact=new_high,
        filtered_not_relevant=filtered or 0,
        pricing_watch=_pricing_watch(session, now),
        pipeline=_pipeline_info(session),
    )


def _headline(session: DbSession) -> dict[str, Any]:
    """Headline + overview of the latest daily_brief, with their sources. Read-time check: a
    sentence is shown only while every signal it cites is still usable."""
    report = session.scalar(
        select(Report)
        .where(Report.kind == ReportKind.DAILY_BRIEF)
        .order_by(Report.created_at.desc(), Report.id.desc())
        .limit(1)
    )
    content = report.content if report else {}
    headline_ids = _ids(content.get("headline_signal_ids"))
    sentences = [
        (s.get("text"), _ids(s.get("signal_ids")))
        for s in content.get("overview_sentences") or []
        if isinstance(s, dict)
    ]
    refs = citation_refs(session, [*headline_ids, *(i for _, ids in sentences for i in ids)])

    def cited(ids: list[int]) -> list[CitationRef] | None:
        return [refs[i] for i in ids] if ids and all(i in refs for i in ids) else None

    headline = content.get("headline")
    headline_refs = cited(headline_ids)
    if not (isinstance(headline, str) and headline.strip() and headline_refs):
        headline, headline_refs = None, []
    overview = [
        CitedSentenceOut(text=text, citations=c)
        for text, ids in sentences
        if isinstance(text, str) and text.strip() and (c := cited(ids))
    ]
    return {
        "headline": headline,
        "headline_citations": headline_refs,
        "overview": " ".join(o.text for o in overview) or None,
        "overview_sentences": overview,
    }


def _ids(value: Any) -> list[int]:
    return [int(v) for v in value if isinstance(v, int)] if isinstance(value, list) else []


def _pricing_watch(session: DbSession, now: Now) -> list[PricingItem]:
    """Latest relevant page-diff signal per watched page; highest impact first, then newest."""
    latest = (
        select(Signal.id)
        .where(
            visible(),
            is_page_diff(),
            Signal.relevant.is_(True),
            Signal.verification != Verification.UNSUPPORTED,
            event_time() >= now - timedelta(days=PRICING_WATCH_DAYS),
            event_time() <= now,
        )
        .ext(distinct_on(Signal.source_id))
        .order_by(Signal.source_id, event_time().desc(), Signal.id.desc())
    ).subquery()
    rows = session.scalars(
        signals_query()
        .where(Signal.id.in_(select(latest.c.id)))
        .order_by(Signal.impact.desc().nulls_last(), event_time().desc(), Signal.id.desc())
        .limit(PRICING_WATCH_MAX)
    ).all()
    return [_pricing_item(sig, out) for sig, out in zip(rows, to_outs(session, rows), strict=True)]


def _pricing_item(sig: Signal, out: SignalOut) -> PricingItem:
    """Verified model-proposed changes (meta.price_changes, D52) first, the parser (D48) as the
    fallback. Both are short strings checked against the stored diff."""
    verified = [
        PriceChange(label=c["label"], before=c["before"], after=c["after"])
        for c in (sig.meta or {}).get("price_changes") or []
        if isinstance(c, dict) and {"label", "before", "after"} <= c.keys()
    ]
    if verified:
        return PricingItem(signal=out, changes=verified, changes_source="verified_llm")
    parsed = price_changes(sig.raw_text)
    return PricingItem(signal=out, changes=parsed, changes_source="parser" if parsed else None)


def _pipeline_info(session: DbSession) -> PipelineInfo:
    # Eval runs share the table; they carry counts.kind and are not pipeline runs.
    run = session.scalar(
        select(PipelineRun)
        .where(~PipelineRun.counts.has_key("kind"))
        .order_by(PipelineRun.started_at.desc(), PipelineRun.id.desc())
        .limit(1)
    )
    feeds = session.scalar(
        select(func.count())
        .select_from(Source)
        .where(Source.is_active, Source.fetch_method.in_(FEED_METHODS))
    )
    pages = session.scalar(
        select(func.count())
        .select_from(Source)
        .where(Source.is_active, Source.fetch_method == FetchMethod.PAGE_DIFF)
    )
    return PipelineInfo(
        last_run=(
            LastRun(
                id=run.id,
                started_at=run.started_at,
                finished_at=run.finished_at,
                status=run.status,
            )  # fmt: skip
            if run
            else None
        ),
        active_feeds=feeds or 0,
        watched_pages=pages or 0,
    )
