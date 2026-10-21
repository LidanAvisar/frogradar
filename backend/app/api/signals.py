"""GET /api/signals (filtered, paginated, full-text) and GET /api/signals/{id}."""

from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, not_, select

from app.api.deps import DbSession, Now
from app.api.queries import (
    entity_and_products,
    event_time,
    is_page_diff,
    signals_query,
    to_outs,
    visible,
)
from app.api.schemas import SignalOut, SignalPage
from app.db.enums import SignalCategory, Verification
from app.db.models import Signal, signal_entities

router = APIRouter(prefix="/api", tags=["signals"])

WINDOWS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}


@router.get("/signals", response_model=SignalPage)
def list_signals(
    session: DbSession,
    now: Now,
    entity: Annotated[
        str | None, Query(description="Entity slug (a company includes its products)")
    ] = None,
    category: SignalCategory | None = None,
    min_impact: Annotated[int | None, Query(ge=1, le=5)] = None,
    relevant: bool | None = True,
    since: datetime | None = None,
    until: datetime | None = None,
    window: Annotated[
        Literal["24h", "7d", "30d", "90d"] | None,
        Query(description="Relative window ending now (server clock, as the Brief stats)"),
    ] = None,
    kind: Annotated[
        Literal["article", "page_diff"] | None, Query(description="page_diff = pricing pages")
    ] = None,
    include_unsupported: Annotated[
        bool, Query(description="Include signals whose summary the verifier did not support")
    ] = False,
    q: Annotated[str | None, Query(max_length=200, description="Full-text search")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SignalPage:
    conds = [visible()]
    if entity:
        ids = entity_and_products(session, entity)
        if not ids:
            return SignalPage(items=[], total=0, page=page, page_size=page_size)
        conds.append(
            Signal.id.in_(
                select(signal_entities.c.signal_id).where(signal_entities.c.entity_id.in_(ids))
            )
        )
    if category:
        conds.append(Signal.category == category)
    if min_impact:
        conds.append(Signal.impact >= min_impact)
    if relevant is not None:
        conds.append(Signal.relevant.is_(relevant))
    if not include_unsupported:
        conds.append(Signal.verification != Verification.UNSUPPORTED)
    if kind:
        # coalesce: meta.kind is NULL for articles, and NOT (NULL OR false) is NULL.
        pd = func.coalesce(is_page_diff(), False)
        conds.append(pd if kind == "page_diff" else not_(pd))
    if window:
        conds.append(event_time() >= now - WINDOWS[window])
        conds.append(event_time() <= now)
    if since:
        conds.append(event_time() >= since)
    if until:
        conds.append(event_time() <= until)
    tsquery = None
    if q and q.strip():
        tsquery = func.websearch_to_tsquery("english", q.strip())
        conds.append(Signal.search_tsv.op("@@")(tsquery))

    total = session.scalar(select(func.count()).select_from(Signal).where(*conds)) or 0
    order = [event_time().desc(), Signal.id.desc()]
    if tsquery is not None:
        order.insert(0, func.ts_rank(Signal.search_tsv, tsquery).desc())
    rows = session.scalars(
        signals_query()
        .where(*conds)
        .order_by(*order)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return SignalPage(items=to_outs(session, rows), total=total, page=page, page_size=page_size)


@router.get("/signals/{signal_id}", response_model=SignalOut, responses={404: {}})
def get_signal(signal_id: int, session: DbSession) -> SignalOut:
    sig = session.scalar(signals_query().where(Signal.id == signal_id, visible()))
    if sig is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return to_outs(session, [sig])[0]
