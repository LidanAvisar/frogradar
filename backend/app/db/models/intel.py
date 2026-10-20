"""What we learned: signals, insights, page snapshots, analyst feedback, generated reports."""

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Table,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CHAR, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.catalog import load_llm_routing
from app.db.base import Base, created_at, enum_type, pk, updated_at
from app.db.enums import (
    FeedbackAction,
    FeedbackTarget,
    InsightType,
    ReasonCode,
    ReportKind,
    ReviewStatus,
    SignalCategory,
    Verification,
)
from app.db.models.catalog import Entity, Source

# Fixed column size, read from config/llm_routing.yaml. Changing it needs a migration + re-embed.
EMBEDDING_DIM = load_llm_routing().embedding.dimensions

signal_entities = Table(
    "signal_entities",
    Base.metadata,
    Column("signal_id", BigInteger, ForeignKey("signals.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "entity_id",
        BigInteger,
        ForeignKey("entities.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
    # True = the signal is about this entity; False = the entity is only mentioned.
    Column("is_primary", Boolean, nullable=False, server_default=text("false")),
)

insight_signals = Table(
    "insight_signals",
    Base.metadata,
    Column(
        "insight_id", BigInteger, ForeignKey("insights.id", ondelete="CASCADE"), primary_key=True
    ),
    Column(
        "signal_id",
        BigInteger,
        ForeignKey("signals.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)


class Signal(Base):
    """One event from one source. Inserted at collection; enrich/verify fill in the rest."""

    __tablename__ = "signals"
    __table_args__ = (
        CheckConstraint("impact BETWEEN 1 AND 5", name="impact_range"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        Index(
            "ix_signals_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_signals_search_tsv", "search_tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = pk()
    source_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sources.id", ondelete="SET NULL"), index=True
    )
    mission_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("missions.id", ondelete="SET NULL"), index=True
    )
    run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pipeline_runs.id", ondelete="SET NULL"), index=True
    )
    url: Mapped[str]
    canonical_url: Mapped[str] = mapped_column(index=True)
    # sha256 of normalized text, see app.pipeline.hashing.content_hash
    content_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    title: Mapped[str]
    raw_text: Mapped[str]
    published_at: Mapped[datetime | None] = mapped_column(index=True)
    fetched_at: Mapped[datetime] = created_at()
    category: Mapped[SignalCategory | None] = mapped_column(
        enum_type(SignalCategory, "signal_category")
    )
    summary: Mapped[str | None]
    why_jfrog_cares: Mapped[str | None]
    impact: Mapped[int | None] = mapped_column(SmallInteger)
    confidence: Mapped[float | None]
    evidence_quote: Mapped[str | None]
    verification: Mapped[Verification] = mapped_column(
        enum_type(Verification, "verification"), server_default=Verification.PENDING.value
    )
    verification_notes: Mapped[str | None]
    # True = vendor claim ("fastest scanner"), False = verifiable fact (release, price change).
    is_claim: Mapped[bool] = mapped_column(server_default=text("false"))
    # Points at the canonical signal of this duplicate cluster.
    dedup_cluster_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("signals.id", ondelete="SET NULL"), index=True
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[ReviewStatus] = mapped_column(
        enum_type(ReviewStatus, "status"), server_default=ReviewStatus.NEW.value, index=True
    )
    enriched_at: Mapped[datetime | None]
    search_tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english'::regconfig, "
            "coalesce(title, '') || ' ' || coalesce(summary, ''))",
            persisted=True,
        ),
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    source: Mapped[Source | None] = relationship()
    entities: Mapped[list[Entity]] = relationship(secondary=signal_entities)


class Insight(Base):
    """Several signals fused into one analyst-facing conclusion."""

    __tablename__ = "insights"
    __table_args__ = (
        CheckConstraint("impact BETWEEN 1 AND 5", name="impact_range"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )

    id: Mapped[int] = pk()
    title: Mapped[str]
    narrative: Mapped[str]
    type: Mapped[InsightType] = mapped_column(enum_type(InsightType, "insight_type"), index=True)
    confidence: Mapped[float]
    impact: Mapped[int | None] = mapped_column(SmallInteger)
    status: Mapped[ReviewStatus] = mapped_column(
        enum_type(ReviewStatus, "status"), server_default=ReviewStatus.NEW.value, index=True
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    signals: Mapped[list[Signal]] = relationship(secondary=insight_signals)


class PageSnapshot(Base):
    """Latest-known text of a watched page (pricing, features) for the diff watcher."""

    __tablename__ = "page_snapshots"
    __table_args__ = (
        Index("ix_page_snapshots_source_fetched", "source_id", text("fetched_at DESC")),
    )

    id: Mapped[int] = pk()
    source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sources.id", ondelete="CASCADE"))
    content_hash: Mapped[str] = mapped_column(CHAR(64))
    text: Mapped[str]
    fetched_at: Mapped[datetime] = created_at()


class Feedback(Base):
    """Analyst action on any target. Polymorphic target, so no FK on target_id."""

    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),
        Index("ix_feedback_target", "target_type", "target_id"),
    )

    id: Mapped[int] = pk()
    target_type: Mapped[FeedbackTarget] = mapped_column(
        enum_type(FeedbackTarget, "feedback_target")
    )
    target_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[FeedbackAction] = mapped_column(enum_type(FeedbackAction, "feedback_action"))
    rating: Mapped[int | None] = mapped_column(SmallInteger)
    reason_code: Mapped[ReasonCode | None] = mapped_column(enum_type(ReasonCode, "reason_code"))
    correction: Mapped[dict[str, Any] | None]
    comment: Mapped[str | None]
    analyst: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), index=True)


class Report(Base):
    """Stored LLM output: daily brief, battlecard, digest, mission report."""

    __tablename__ = "reports"
    __table_args__ = (
        Index("ix_reports_kind_entity_created", "kind", "entity_id", text("created_at DESC")),
    )

    id: Mapped[int] = pk()
    kind: Mapped[ReportKind] = mapped_column(enum_type(ReportKind, "report_kind"))
    entity_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("entities.id", ondelete="SET NULL")
    )
    period_start: Mapped[datetime | None]
    period_end: Mapped[datetime | None]
    content: Mapped[dict[str, Any]]
    signal_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), server_default="{}")
    run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pipeline_runs.id", ondelete="SET NULL"), index=True
    )
    model: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = created_at()
