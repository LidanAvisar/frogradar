"""GET /api/brief – Morning Brief: top developments, what's new, pricing watch, pipeline."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on

from app.api.deps import DbSession, Now
from app.api.prices import price_changes
from app.api.queries import (
    SHOWN_VERIFICATIONS,
    canonical,
    entity_refs,
    event_time,
    is_page_diff,
    signals_query,
    to_outs,
    visible,
)
from app.api.schemas import (
    BriefOut,
    BriefWindow,
    LastRun,
    PipelineInfo,
    PricingItem,
    RankedSignalOut,
    SinceYesterdayItem,
)
from app.db.enums import FetchMethod, ReportKind, Verification
from app.db.models import PipelineRun, Report, Signal, Source
from app.pipeline.scope import in_scope

router = APIRouter(prefix="/api", tags=["brief"])

SINCE_YESTERDAY_MAX = 10
PRICING_WATCH_DAYS = 90
PRICING_WATCH_MAX = 4
FEED_METHODS = (FetchMethod.RSS, FetchMethod.GITHUB, FetchMethod.WEB)


@router.get("/brief", response_model=BriefOut)
def get_brief(
    session: DbSession,
    now: Now,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> BriefOut:
    since = now - timedelta(days=days)

    # Ranked: relevant, verified (supported or partial), impact × confidence.
    top_rows = session.scalars(
        signals_query()
        .where(
            visible(),
            Signal.relevant.is_(True),
            Signal.verification.in_(SHOWN_VERIFICATIONS),
            Signal.impact.is_not(None),
            Signal.confidence.is_not(None),
            event_time() >= since,
            event_time() <= now,
        )
        .order_by((Signal.impact * Signal.confidence).desc(), event_time().desc(), Signal.id.desc())
        .limit(limit)
    ).all()
    top = [
        RankedSignalOut(rank=i, **out.model_dump())
        for i, out in enumerate(to_outs(session, top_rows), start=1)
    ]

    # New = published in the last 24h. Backfilled old articles fetched today don't count;
    # fetched_at is used only when the source gave no publish date.
    new_rows = session.scalars(
        select(Signal)
        .where(
            visible(),
            Signal.relevant.is_(True),
            Signal.verification != Verification.UNSUPPORTED,
            event_time() >= now - timedelta(hours=24),
            event_time() <= now,
        )
        .order_by(Signal.impact.desc().nulls_last(), event_time().desc(), Signal.id.desc())
        .limit(SINCE_YESTERDAY_MAX)
    ).all()
    refs = entity_refs(session, [s.id for s in new_rows])
    since_yesterday = [
        SinceYesterdayItem(
            id=s.id,
            title=s.title,
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
        filtered_not_relevant=filtered or 0,
        pricing_watch=_pricing_watch(session, now),
        pipeline=_pipeline_info(session),
    )


def _headline(session: DbSession) -> dict[str, str | None]:
    report = session.scalar(
        select(Report)
        .where(Report.kind == ReportKind.DAILY_BRIEF)
        .order_by(Report.created_at.desc(), Report.id.desc())
        .limit(1)
    )
    content = report.content if report else {}
    headline, overview = content.get("headline"), content.get("overview")
    return {
        "headline": headline if isinstance(headline, str) and headline.strip() else None,
        "overview": overview if isinstance(overview, str) and overview.strip() else None,
    }


def _pricing_watch(session: DbSession, now: Now) -> list[PricingItem]:
    """Latest relevant page-diff signal per watched page."""
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
        .order_by(event_time().desc(), Signal.id.desc())
        .limit(PRICING_WATCH_MAX)
    ).all()
    return [
        PricingItem(signal=out, changes=price_changes(sig.raw_text))
        for sig, out in zip(rows, to_outs(session, rows), strict=True)
    ]


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
