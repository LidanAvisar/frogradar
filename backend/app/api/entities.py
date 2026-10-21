"""GET /api/entities (tracked companies with signal counts) and GET /api/nav (sidebar counts)."""

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.api.deps import DbSession
from app.api.queries import event_time, visible
from app.api.schemas import EntityOut, NavCounts
from app.db.enums import EntityReviewStatus, EntityType, ReviewStatus
from app.db.models import Entity, Signal, signal_entities

router = APIRouter(prefix="/api", tags=["entities"])


@router.get("/entities", response_model=list[EntityOut])
def list_entities(session: DbSession) -> list[EntityOut]:
    linked = aliased(Entity)
    # A product's signals count for its company: company id = parent_id, else the entity itself.
    company_id = func.coalesce(linked.parent_id, linked.id)
    stats = (
        select(
            company_id.label("company_id"),
            func.count(func.distinct(Signal.id)).label("n"),
            func.max(event_time()).label("last_at"),
        )
        .select_from(signal_entities)
        .join(linked, linked.id == signal_entities.c.entity_id)
        .join(Signal, Signal.id == signal_entities.c.signal_id)
        .where(visible(), Signal.relevant.is_(True))
        .group_by(company_id)
    ).subquery()
    rows = session.execute(
        select(Entity, stats.c.n, stats.c.last_at)
        .outerjoin(stats, stats.c.company_id == Entity.id)
        .where(
            Entity.type == EntityType.COMPANY,
            Entity.is_tracked,
            Entity.review_status == EntityReviewStatus.APPROVED,
        )
        .order_by(Entity.is_self.desc(), func.coalesce(stats.c.n, 0).desc(), Entity.name)
    ).all()
    return [
        EntityOut(
            slug=e.slug,
            name=e.name,
            type=e.type,
            relationship=e.relationship,
            is_self=e.is_self,
            signal_count=n or 0,
            last_signal_at=last_at,
        )  # fmt: skip
        for e, n, last_at in rows
    ]


@router.get("/nav", response_model=NavCounts)
def nav_counts(session: DbSession) -> NavCounts:
    base = select(func.count()).select_from(Signal).where(visible(), Signal.relevant.is_(True))
    return NavCounts(
        signals=session.scalar(base) or 0,
        review_queue=session.scalar(base.where(Signal.status == ReviewStatus.NEW)) or 0,
    )
