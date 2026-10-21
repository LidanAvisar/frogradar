"""Who we track and where we look: entities, sources, the capability taxonomy and matrix."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as orm_relationship

from app.db.base import Base, created_at, enum_type, pk, updated_at
from app.db.enums import (
    CapabilityLevel,
    DiscoveredBy,
    EntityReviewStatus,
    EntityType,
    FetchMethod,
    Relationship,
    Segment,
    SourceKind,
)

_SEGMENT_VALUES = ", ".join(f"'{s.value}'" for s in Segment)


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (
        CheckConstraint(f"segments <@ ARRAY[{_SEGMENT_VALUES}]::varchar[]", name="segments_valid"),
        # Only JFrog itself may be marked as "self".
        Index("uq_entities_single_self", "is_self", unique=True, postgresql_where=text("is_self")),
        Index("ix_entities_aliases", "aliases", postgresql_using="gin"),
    )

    id: Mapped[int] = pk()
    type: Mapped[EntityType] = mapped_column(enum_type(EntityType, "entity_type"))
    name: Mapped[str]
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}")
    # Non-default entity-linking rules by term (name or alias), from competitors.yaml (seed-owned),
    # e.g. {"Socket": {"enabled": true, "case_sensitive": true, "min_mentions": 3, ...}}
    match_rules: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    segments: Mapped[list[str]] = mapped_column(ARRAY(String(32)), server_default="{}")
    website: Mapped[str | None]
    description: Mapped[str | None]
    # End of life (product no longer sold or supported): prompts must never recommend it.
    discontinued_on: Mapped[date | None]
    relationship: Mapped[Relationship] = mapped_column(
        enum_type(Relationship, "relationship"), server_default=Relationship.COMPETITOR.value
    )
    is_tracked: Mapped[bool] = mapped_column(server_default=text("true"))
    is_self: Mapped[bool] = mapped_column(server_default=text("false"))
    review_status: Mapped[EntityReviewStatus] = mapped_column(
        enum_type(EntityReviewStatus, "review_status"),
        server_default=EntityReviewStatus.CANDIDATE.value,
    )
    discovered_by: Mapped[DiscoveredBy] = mapped_column(
        enum_type(DiscoveredBy, "discovered_by"), server_default=DiscoveredBy.CONFIG.value
    )
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    parent: Mapped["Entity | None"] = orm_relationship(remote_side=[id], back_populates="products")
    products: Mapped[list["Entity"]] = orm_relationship(back_populates="parent")


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("trust_score >= 0 AND trust_score <= 1", name="trust_score_range"),
        Index("ix_sources_active_method", "is_active", "fetch_method"),
    )

    id: Mapped[int] = pk()
    entity_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("entities.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str]
    url: Mapped[str] = mapped_column(unique=True)
    kind: Mapped[SourceKind] = mapped_column(enum_type(SourceKind, "source_kind"))
    fetch_method: Mapped[FetchMethod] = mapped_column(enum_type(FetchMethod, "fetch_method"))
    trust_score: Mapped[float]
    options: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    discovered_by: Mapped[DiscoveredBy] = mapped_column(
        enum_type(DiscoveredBy, "discovered_by"), server_default=DiscoveredBy.CONFIG.value
    )
    last_fetched_at: Mapped[datetime | None]
    # Conditional GET validators from the last successful fetch (DB-owned; seed never touches them).
    http_etag: Mapped[str | None]
    http_last_modified: Mapped[str | None]
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    entity: Mapped[Entity | None] = orm_relationship()


class Capability(Base):
    __tablename__ = "capabilities"

    id: Mapped[int] = pk()
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str]
    segment: Mapped[Segment] = mapped_column(enum_type(Segment, "segment"))
    description: Mapped[str]
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class CapabilityMatrix(Base):
    """Entity x capability -> level, with the signal that justifies it."""

    __tablename__ = "capability_matrix"
    __table_args__ = (UniqueConstraint("entity_id", "capability_id"),)

    id: Mapped[int] = pk()
    entity_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("entities.id", ondelete="CASCADE")
    )
    capability_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("capabilities.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[CapabilityLevel] = mapped_column(
        enum_type(CapabilityLevel, "capability_level"),
        server_default=CapabilityLevel.UNKNOWN.value,
    )
    # Level before the last change and when it changed (battlecard "changed recently" flag).
    previous_level: Mapped[CapabilityLevel | None] = mapped_column(
        enum_type(CapabilityLevel, "previous_capability_level")
    )
    level_changed_at: Mapped[datetime | None]
    notes: Mapped[str | None]
    evidence_signal_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("signals.id", ondelete="SET NULL")
    )
    last_verified_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = updated_at()
