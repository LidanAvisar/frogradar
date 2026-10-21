"""Competitor Radar (S11): radar_candidates, repo_star_snapshots, discovery fetch methods,
radar feedback (target radar_candidate, action watch, dismiss reason codes).

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-21

"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1536  # = config/llm_routing.yaml embedding.dimensions (checked at startup)

OLD_FETCH = ("rss", "github", "page_diff", "web")
NEW_FETCH = (*OLD_FETCH, "hn_search", "github_search", "cncf_landscape")
OLD_TARGET = ("signal", "insight", "capability_matrix", "mission")
NEW_TARGET = (*OLD_TARGET, "radar_candidate")
OLD_ACTION = ("approve", "reject", "edit", "rate")
NEW_ACTION = (*OLD_ACTION, "watch")
OLD_REASON = (
    "not_relevant", "wrong_entity", "wrong_category", "impact_too_high", "impact_too_low",
    "inaccurate_summary", "unsupported_claim", "duplicate", "outdated", "other",
)  # fmt: skip
NEW_REASON = (*OLD_REASON[:-1], "not_a_competitor", "too_small", "already_tracked", "other")
SEGMENTS = ("artifact_management", "supply_chain_security", "mlops", "cicd", "distribution")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _replace_check(table: str, name: str, column: str, values: Sequence[str]) -> None:
    op.drop_constraint(op.f(name), table, type_="check")
    op.create_check_constraint(op.f(name), table, _in(column, values))


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def upgrade() -> None:
    _replace_check("sources", "ck_sources_fetch_method", "fetch_method", NEW_FETCH)
    _replace_check("feedback", "ck_feedback_feedback_target", "target_type", NEW_TARGET)
    _replace_check("feedback", "ck_feedback_feedback_action", "action", NEW_ACTION)
    _replace_check("feedback", "ck_feedback_reason_code", "reason_code", NEW_REASON)

    op.create_table(
        "radar_candidates",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column(
            "kind", _enum("radar_kind", "company", "product", "open_source_project"), nullable=False
        ),
        sa.Column("homepage", sa.Text(), nullable=True),
        sa.Column("repo_url", sa.Text(), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "mention_signal_ids",
            postgresql.ARRAY(sa.BigInteger()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "source_ids", postgresql.ARRAY(sa.BigInteger()), server_default="{}", nullable=False
        ),
        sa.Column(
            "metrics", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "segments", postgresql.ARRAY(sa.String(length=32)), server_default="{}", nullable=False
        ),
        sa.Column(
            "status",
            _enum("radar_status", "new", "watching", "approved", "dismissed"),
            server_default="new",
            nullable=False,
        ),
        sa.Column("dismiss_reason", _enum("dismiss_reason", *NEW_REASON), nullable=True),
        sa.Column("dismiss_comment", sa.Text(), nullable=True),
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "profile_status",
            _enum("profile_status", "none", "ok", "rejected"),
            server_default="none",
            nullable=False,
        ),
        sa.Column("profile_signal_hash", sa.String(length=64), nullable=True),
        sa.Column("score", sa.Double(), nullable=True),
        sa.Column("score_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("merged_into_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "merges", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("approved_entity_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "meta", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "name_embedding", pgvector.sqlalchemy.vector.VECTOR(dim=EMBEDDING_DIM), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"segments <@ ARRAY[{', '.join(repr(s) for s in SEGMENTS)}]::varchar[]",
            name=op.f("ck_radar_candidates_segments_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["approved_entity_id"],
            ["entities.id"],
            name=op.f("fk_radar_candidates_approved_entity_id_entities"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["merged_into_id"],
            ["radar_candidates.id"],
            name=op.f("fk_radar_candidates_merged_into_id_radar_candidates"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_radar_candidates")),
    )
    op.create_index(
        "uq_radar_candidates_key_live",
        "radar_candidates",
        ["key"],
        unique=True,
        postgresql_where=sa.text("merged_into_id IS NULL"),
    )
    op.create_index(
        "ix_radar_candidates_status_score",
        "radar_candidates",
        ["status", sa.text("score DESC")],
        unique=False,
    )
    op.create_index(
        op.f("ix_radar_candidates_merged_into_id"),
        "radar_candidates",
        ["merged_into_id"],
        unique=False,
    )

    op.create_table(
        "repo_star_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("repo", sa.String(length=200), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_day", sa.Date(), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("forks", sa.Integer(), nullable=True),
        sa.Column("repo_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_repo_star_snapshots_run_id_pipeline_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repo_star_snapshots")),
        sa.UniqueConstraint(
            "repo", "captured_day", name=op.f("uq_repo_star_snapshots_repo_captured_day")
        ),
    )
    op.create_index(
        "ix_repo_star_snapshots_repo_captured",
        "repo_star_snapshots",
        ["repo", "captured_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_repo_star_snapshots_repo_captured", table_name="repo_star_snapshots")
    op.drop_table("repo_star_snapshots")
    op.drop_index(op.f("ix_radar_candidates_merged_into_id"), table_name="radar_candidates")
    op.drop_index("ix_radar_candidates_status_score", table_name="radar_candidates")
    op.drop_index("uq_radar_candidates_key_live", table_name="radar_candidates")
    op.drop_table("radar_candidates")
    # Rows using the new values would violate the old checks: remove them first.
    op.execute(
        "DELETE FROM feedback WHERE target_type = 'radar_candidate' OR action = 'watch' "
        "OR reason_code IN ('not_a_competitor', 'too_small', 'already_tracked')"
    )
    _replace_check("feedback", "ck_feedback_reason_code", "reason_code", OLD_REASON)
    _replace_check("feedback", "ck_feedback_feedback_action", "action", OLD_ACTION)
    _replace_check("feedback", "ck_feedback_feedback_target", "target_type", OLD_TARGET)
    op.execute(
        "UPDATE sources SET is_active = false, fetch_method = 'web' "
        "WHERE fetch_method IN ('hn_search', 'github_search', 'cncf_landscape')"
    )
    _replace_check("sources", "ck_sources_fetch_method", "fetch_method", OLD_FETCH)
