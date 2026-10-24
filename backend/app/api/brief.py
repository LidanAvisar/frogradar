"""GET /api/brief – Morning Brief: top developments, what's new, pricing watch, pipeline."""

from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import aliased, selectinload

from app.api.deps import DbSession, Now
from app.api.prices import price_changes
from app.api.products import product_matcher
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
from app.api.radar import teaser
from app.api.schemas import (
    BriefOut,
    BriefStats,
    BriefWindow,
    LastRun,
    PipelineInfo,
    PriceChange,
    PricingItem,
    RankedSignalOut,
    SignalOut,
    SinceYesterdayItem,
    StoryRowOut,
    TopStoryOut,
)
from app.core.catalog import load_discovery
from app.db.enums import FetchMethod, ReportKind, Verification
from app.db.models import Entity, PipelineRun, Report, Signal, Source, signal_entities
from app.pipeline.scope import in_scope, not_discovery, not_mission, usable
from app.pipeline.synthesis.inputs import display_title

router = APIRouter(prefix="/api", tags=["brief"])

SINCE_YESTERDAY_MAX = 10
PRICING_WATCH_DAYS = 90
PRICING_WATCH_MAX = 4
HIGH_IMPACT = 4
PRICE_CHANGE_DAYS = 7
MAX_STORY_IDS = 10  # extra ranked rows fetched so the developments list stays full
FEED_METHODS = (FetchMethod.RSS, FetchMethod.GITHUB, FetchMethod.WEB)


@router.get("/brief", response_model=BriefOut)
def get_brief(
    session: DbSession,
    now: Now,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    limit: Annotated[int, Query(ge=1, le=20)] = 4,
) -> BriefOut:
    # Anchor: now, unless nothing ranks in the window (e.g. only older seed data, or the pipeline
    # hasn't run for a while). Then the Brief is the latest available day, labelled with its date
    # (stale=true), instead of an empty page or old news presented as today's (D83).
    generated_at = now
    as_of = _anchor(session, now, days)
    stale = as_of != now
    now = as_of  # every window below ends at the anchor
    since = now - timedelta(days=days)

    # Ranked: relevant, verified (supported or partial), impact × confidence (scope.ranked).
    ranked_rows = session.scalars(
        ranked(since, now).options(selectinload(Signal.source)).limit(limit + MAX_STORY_IDS)
    ).all()
    story = _top_story(session, ranked_rows, now)
    story_ids = set(story.signal_ids) if story else set()
    rest = [s for s in ranked_rows if s.id not in story_ids][:limit]
    developments = [
        RankedSignalOut(rank=i, **out.model_dump())
        for i, out in enumerate(to_outs(session, rest), start=1)
    ]

    # New = published in the last 24h. Backfilled old articles fetched today don't count;
    # fetched_at is used only when the source gave no publish date.
    is_new = (
        visible(),
        not_mission(),  # findings have no publish date: fetched today is not news today (D82)
        Signal.relevant.is_(True),
        Signal.verification != Verification.UNSUPPORTED,
        event_time() >= now - timedelta(hours=24),
        event_time() <= now,
    )
    new_total, new_high = session.execute(
        select(func.count(), func.count().filter(Signal.impact >= HIGH_IMPACT)).where(*is_new)
    ).one()
    # The list leaves out what the page already shows above; the stats count everything new.
    shown = [*story_ids, *(d.id for d in developments)]
    new_rows = session.scalars(
        select(Signal)
        .where(*is_new, Signal.id.not_in(shown))
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
    price_changes = session.scalar(
        select(func.count()).where(
            visible(),
            is_page_diff(),
            Signal.relevant.is_(True),
            Signal.verification != Verification.UNSUPPORTED,
            event_time() >= now - timedelta(days=PRICE_CHANGE_DAYS),
            event_time() <= now,
        )
    )

    filtered = session.scalar(
        select(func.count())
        .select_from(Signal)
        .where(
            in_scope(),
            canonical(),
            not_discovery(),  # discovery items filtered by triage are radar noise, not news
            Signal.relevant.is_(False),
            event_time() >= since,
        )
    )

    return BriefOut(
        generated_at=generated_at,
        as_of=as_of,
        stale=stale,
        window=BriefWindow(days=days, since=since),
        stats=BriefStats(
            new_signals=new_total,
            high_impact=new_high,
            price_changes=price_changes or 0,
            price_change_days=PRICE_CHANGE_DAYS,
        ),
        top_story=story,
        developments=developments,
        since_yesterday=since_yesterday,
        filtered_not_relevant=filtered or 0,
        pricing_watch=_pricing_watch(session, now),
        pipeline=_pipeline_info(session),
        radar=teaser(session, load_discovery().radar.teaser_count),
    )


def _anchor(session: DbSession, now: datetime, days: int) -> datetime:
    """`now`, or the event time of the newest rankable signal when none falls in the window."""
    newest = session.scalar(
        select(func.max(event_time())).where(
            usable(),
            not_mission(),
            Signal.impact.is_not(None),
            Signal.confidence.is_not(None),
            event_time() <= now,
        )
    )
    if newest is None or newest >= now - timedelta(days=days):
        return now
    return newest


def _top_story(session: DbSession, ranked_rows: list[Signal], now: datetime) -> TopStoryOut | None:
    """The daily_brief's top story from the last 24h (relative to the Brief's anchor), if its
    required rows still check out (read-time: every cited signal still usable); else the
    top-ranked signal (no suggested move). An older daily brief is never shown as today's."""
    report = session.scalar(
        select(Report)
        .where(
            Report.kind == ReportKind.DAILY_BRIEF,
            Report.created_at >= now - timedelta(hours=24),
        )
        .order_by(Report.created_at.desc(), Report.id.desc())
        .limit(1)
    )
    rows_in = (report.content or {}).get("top_story") if report else None
    if isinstance(rows_in, dict):
        story = _from_report(session, rows_in)
        if story is not None:
            return story
    if not ranked_rows:
        return None
    lead = ranked_rows[0]
    out = to_outs(session, [lead])[0]
    ref = citation_refs(session, [lead.id]).get(lead.id)
    cites = [ref] if ref else []
    why = lead.takeaway or first_sentence(lead.why_jfrog_cares)
    named = product_matcher(session)
    return TopStoryOut(
        source="top_signal",
        title=out.title,
        title_citations=cites,
        what_happened=StoryRowOut(text=lead.summary, citations=cites) if lead.summary else None,
        why_it_matters=StoryRowOut(text=why, citations=cites) if why else None,
        suggested_move=None,
        signal=out,
        company=_company(session, [lead.id]),
        affects=named(f"{why or ''}"),
        signal_ids=[lead.id],
    )


def _from_report(session: DbSession, rows_in: dict[str, Any]) -> TopStoryOut | None:
    rows = {
        key: (row.get("text"), _ids(row.get("signal_ids")))
        for key, row in rows_in.items()
        if isinstance(row, dict) and isinstance(row.get("text"), str) and row["text"].strip()
    }
    all_ids = [i for _, ids in rows.values() for i in ids]
    refs = citation_refs(session, all_ids)

    def row(key: str) -> StoryRowOut | None:
        if key not in rows:
            return None
        text, ids = rows[key]
        if not ids or not all(i in refs for i in ids):
            return None
        return StoryRowOut(text=text, citations=[refs[i] for i in ids])

    title, what, why = row("title"), row("what_happened"), row("why_it_matters_for_jfrog")
    if title is None or what is None or why is None:
        return None
    lead_id = rows["title"][1][0]
    lead = session.scalars(signals_query().where(Signal.id == lead_id)).one()
    used = list(dict.fromkeys(all_ids))
    named = product_matcher(session)
    return TopStoryOut(
        source="daily_brief",
        title=title.text,
        title_citations=title.citations,
        what_happened=what,
        why_it_matters=why,
        suggested_move=row("suggested_move"),
        signal=to_outs(session, [lead])[0],
        company=_company(session, rows["title"][1] + rows["what_happened"][1]),
        affects=named(f"{why.text} {title.text}"),
        signal_ids=[i for i in used if i in refs],
    )


def first_sentence(text: str | None) -> str | None:
    if not text:
        return None
    head, dot, _ = text.partition(". ")
    return head + ("." if dot else "")


def _company(session: DbSession, ids: list[int]) -> str | None:
    """Most frequent company among the cited signals' primary entities (a product counts for
    its company); JFrog itself is skipped. Ties → the first cited."""
    parent = aliased(Entity)
    rows = session.execute(
        select(signal_entities.c.signal_id, func.coalesce(parent.name, Entity.name))
        .join(Entity, Entity.id == signal_entities.c.entity_id)
        .outerjoin(parent, parent.id == Entity.parent_id)
        .where(
            signal_entities.c.signal_id.in_(ids),
            signal_entities.c.is_primary,
            ~Entity.is_self,
            or_(parent.id.is_(None), ~parent.is_self),
        )
    ).all()
    order = {sid: i for i, sid in enumerate(ids)}
    counts: dict[str, tuple[int, int]] = {}
    for sid, name in rows:
        n, first = counts.get(name, (0, len(ids)))
        counts[name] = (n + 1, min(first, order.get(sid, len(ids))))
    if not counts:
        return None
    return min(counts, key=lambda k: (-counts[k][0], counts[k][1]))


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
