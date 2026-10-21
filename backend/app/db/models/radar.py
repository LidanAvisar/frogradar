"""Competitor Radar: candidate players nobody configured, and GitHub star history.

A candidate is never a tracked entity: only an analyst's approve action creates one (D57).
"""

from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Double,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at, enum_type, pk, updated_at
from app.db.enums import ProfileStatus, RadarKind, RadarStatus, ReasonCode, Segment
from app.db.models.intel import EMBEDDING_DIM

_SEGMENT_VALUES = ", ".join(f"'{s.value}'" for s in Segment)


class RadarCandidate(Base):
    __tablename__ = "radar_candidates"
    __table_args__ = (
        CheckConstraint(f"segments <@ ARRAY[{_SEGMENT_VALUES}]::varchar[]", name="segments_valid"),
        # One live candidate per normalized key; merged rows keep their key for the record.
        Index(
            "uq_radar_candidates_key_live",
            "key",
            unique=True,
            postgresql_where=text("merged_into_id IS NULL"),
        ),
        Index("ix_radar_candidates_status_score", "status", text("score DESC")),
    )

    id: Mapped[int] = pk()
    name: Mapped[str]
    # Normalized name (app.pipeline.radar.normalize.name_key): identity for exact merges.
    key: Mapped[str] = mapped_column(String(200))
    kind: Mapped[RadarKind] = mapped_column(enum_type(RadarKind, "radar_kind"))
    homepage: Mapped[str | None]
    repo_url: Mapped[str | None]
    first_seen: Mapped[datetime]
    last_seen: Mapped[datetime]
    mention_signal_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), server_default="{}")
    source_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), server_default="{}")
    # stars, star_velocity, star_velocity_basis, hn_points_max, mentions_recent, ...
    metrics: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    segments: Mapped[list[str]] = mapped_column(ARRAY(String(32)), server_default="{}")
    status: Mapped[RadarStatus] = mapped_column(
        enum_type(RadarStatus, "radar_status"), server_default=RadarStatus.NEW.value
    )
    dismiss_reason: Mapped[ReasonCode | None] = mapped_column(
        enum_type(ReasonCode, "dismiss_reason")
    )
    dismiss_comment: Mapped[str | None]
    profile: Mapped[dict[str, Any] | None]
    profile_status: Mapped[ProfileStatus] = mapped_column(
        enum_type(ProfileStatus, "profile_status"), server_default=ProfileStatus.NONE.value
    )
    # sha256 of the sorted mention ids the profile was built from (regenerate when it changes).
    profile_signal_hash: Mapped[str | None] = mapped_column(String(64))
    score: Mapped[float | None] = mapped_column(Double)
    score_breakdown: Mapped[dict[str, Any] | None]
    merged_into_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("radar_candidates.id", ondelete="SET NULL"), index=True
    )
    # Merge log: [{"from_id", "from_name", "rule": exact|url|fuzzy, "ratio", "cosine", "at"}].
    merges: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    approved_entity_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("entities.id", ondelete="SET NULL")
    )
    # contexts (from new_entities), origins (news_new_entity / github_search / cncf_landscape).
    meta: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    name_embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class RepoStarSnapshot(Base):
    """Stars of a GitHub repo at one time (one row per repo per day): star velocity over runs."""

    __tablename__ = "repo_star_snapshots"
    __table_args__ = (
        UniqueConstraint("repo", "captured_day"),
        Index("ix_repo_star_snapshots_repo_captured", "repo", "captured_at"),
    )

    id: Mapped[int] = pk()
    repo: Mapped[str] = mapped_column(String(200))  # owner/name, lower case
    captured_at: Mapped[datetime]
    captured_day: Mapped[date]  # UTC date of captured_at: one row per repo per day
    stars: Mapped[int]
    forks: Mapped[int | None]
    repo_created_at: Mapped[datetime | None]
    run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pipeline_runs.id", ondelete="SET NULL")
    )
