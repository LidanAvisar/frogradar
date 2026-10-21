"""GET /api/entities (tracked companies with signal counts) and GET /api/nav (sidebar counts)."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.api.deps import DbSession
from app.api.queries import event_time, visible
from app.api.radar import top_section
from app.api.schemas import EntityOut, NavCounts, SelfOut
from app.db.enums import EntityReviewStatus, EntityType, RadarStatus, ReviewStatus, Verification
from app.db.models import Entity, RadarCandidate, Signal, signal_entities

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
        # Same default as GET /api/signals: unsupported summaries are not listed.
        signals=session.scalar(base.where(Signal.verification != Verification.UNSUPPORTED)) or 0,
        review_queue=session.scalar(base.where(Signal.status == ReviewStatus.NEW)) or 0,
        radar_new=session.scalar(
            select(func.count())
            .select_from(RadarCandidate)
            .where(
                RadarCandidate.merged_into_id.is_(None),
                RadarCandidate.status == RadarStatus.NEW,
                top_section(),
            )
        )
        or 0,
    )


# Bare product names shorter than this ("ML") are too generic to highlight on their own.
MIN_BARE_TERM = 4


@router.get("/self", response_model=SelfOut, responses={404: {}})
def get_self(session: DbSession) -> SelfOut:
    company = session.scalar(
        select(Entity).where(Entity.is_self, Entity.type == EntityType.COMPANY)
    )
    if company is None:
        raise HTTPException(status_code=404, detail="No self entity configured")
    # Discontinued products are not highlighted as ours.
    products = session.scalars(
        select(Entity).where(Entity.parent_id == company.id, Entity.discontinued_on.is_(None))
    ).all()
    prefix = f"{company.name} "
    terms: set[str] = set()
    bare: set[str] = set()
    for p in products:
        terms.add(p.name)
        terms.update(a for a in p.aliases if a)
        short = p.name.removeprefix(prefix)
        if short != p.name and len(short) >= MIN_BARE_TERM:
            bare.add(short)

    def longest_first(items: set[str]) -> list[str]:
        return sorted(items, key=lambda t: (-len(t), t))

    return SelfOut(
        slug=company.slug,
        name=company.name,
        product_terms=longest_first(terms),
        bare_terms=longest_first(bare - terms),
    )
