"""Initial schema: pgvector extension and all FrogRadar tables.

Revision ID: 0001
Revises:
Create Date: 2026-10-20 17:21:56.696436

"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "capabilities",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "segment",
            sa.Enum(
                "artifact_management",
                "supply_chain_security",
                "mlops",
                "cicd",
                "distribution",
                name="segment",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=False),
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
            "segment IN ('artifact_management', 'supply_chain_security', 'mlops', 'cicd', 'distribution')",
            name=op.f("ck_capabilities_segment"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capabilities")),
        sa.UniqueConstraint("slug", name=op.f("uq_capabilities_slug")),
    )
    op.create_table(
        "entities",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "company",
                "product",
                name="entity_type",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.String()), server_default="{}", nullable=False),
        sa.Column(
            "segments", postgresql.ARRAY(sa.String(length=32)), server_default="{}", nullable=False
        ),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "relationship",
            sa.Enum(
                "self",
                "competitor",
                "partner",
                "coopetition",
                name="relationship",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            server_default="competitor",
            nullable=False,
        ),
        sa.Column("is_tracked", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_self", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "review_status",
            sa.Enum(
                "candidate",
                "approved",
                "rejected",
                name="review_status",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            server_default="candidate",
            nullable=False,
        ),
        sa.Column(
            "discovered_by",
            sa.Enum(
                "config",
                "radar",
                "mission",
                "analyst",
                name="discovered_by",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            server_default="config",
            nullable=False,
        ),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
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
            "discovered_by IN ('config', 'radar', 'mission', 'analyst')",
            name=op.f("ck_entities_discovered_by"),
        ),
        sa.CheckConstraint(
            "relationship IN ('self', 'competitor', 'partner', 'coopetition')",
            name=op.f("ck_entities_relationship"),
        ),
        sa.CheckConstraint(
            "review_status IN ('candidate', 'approved', 'rejected')",
            name=op.f("ck_entities_review_status"),
        ),
        sa.CheckConstraint(
            "segments <@ ARRAY['artifact_management', 'supply_chain_security', 'mlops', 'cicd', 'distribution']::varchar[]",
            name=op.f("ck_entities_segments_valid"),
        ),
        sa.CheckConstraint("type IN ('company', 'product')", name=op.f("ck_entities_entity_type")),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["entities.id"],
            name=op.f("fk_entities_parent_id_entities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entities")),
        sa.UniqueConstraint("slug", name=op.f("uq_entities_slug")),
    )
    op.create_index(
        "ix_entities_aliases", "entities", ["aliases"], unique=False, postgresql_using="gin"
    )
    op.create_index(op.f("ix_entities_parent_id"), "entities", ["parent_id"], unique=False)
    op.create_index(
        "uq_entities_single_self",
        "entities",
        ["is_self"],
        unique=True,
        postgresql_where=sa.text("is_self"),
    )
    op.create_table(
        "feedback",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "target_type",
            sa.Enum(
                "signal",
                "insight",
                "capability_matrix",
                "mission",
                name="feedback_target",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "action",
            sa.Enum(
                "approve",
                "reject",
                "edit",
                "rate",
                name="feedback_action",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("rating", sa.SmallInteger(), nullable=True),
        sa.Column(
            "reason_code",
            sa.Enum(
                "not_relevant",
                "wrong_entity",
                "wrong_category",
                "impact_too_high",
                "impact_too_low",
                "inaccurate_summary",
                "unsupported_claim",
                "duplicate",
                "outdated",
                "other",
                name="reason_code",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("correction", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("analyst", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('approve', 'reject', 'edit', 'rate')",
            name=op.f("ck_feedback_feedback_action"),
        ),
        sa.CheckConstraint(
            "reason_code IN ('not_relevant', 'wrong_entity', 'wrong_category', 'impact_too_high', 'impact_too_low', 'inaccurate_summary', 'unsupported_claim', 'duplicate', 'outdated', 'other')",
            name=op.f("ck_feedback_reason_code"),
        ),
        sa.CheckConstraint(
            "target_type IN ('signal', 'insight', 'capability_matrix', 'mission')",
            name=op.f("ck_feedback_feedback_target"),
        ),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name=op.f("ck_feedback_rating_range")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feedback")),
    )
    op.create_index(op.f("ix_feedback_created_at"), "feedback", ["created_at"], unique=False)
    op.create_index("ix_feedback_target", "feedback", ["target_type", "target_id"], unique=False)
    op.create_table(
        "insights",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "threat",
                "opportunity",
                "blind_spot",
                "trend",
                name="insight_type",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.Column("impact", sa.SmallInteger(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "new",
                "approved",
                "rejected",
                name="status",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            server_default="new",
            nullable=False,
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
            "status IN ('new', 'approved', 'rejected')", name=op.f("ck_insights_status")
        ),
        sa.CheckConstraint(
            "type IN ('threat', 'opportunity', 'blind_spot', 'trend')",
            name=op.f("ck_insights_insight_type"),
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name=op.f("ck_insights_confidence_range")
        ),
        sa.CheckConstraint("impact BETWEEN 1 AND 5", name=op.f("ck_insights_impact_range")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_insights")),
    )
    op.create_index(op.f("ix_insights_status"), "insights", ["status"], unique=False)
    op.create_index(op.f("ix_insights_type"), "insights", ["type"], unique=False)
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "trigger",
            sa.Enum(
                "scheduled",
                "manual",
                "backfill",
                name="run_trigger",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "succeeded",
                "partial",
                "failed",
                name="run_status",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            server_default="running",
            nullable=False,
        ),
        sa.Column(
            "counts", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "cost_usd", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'partial', 'failed')",
            name=op.f("ck_pipeline_runs_run_status"),
        ),
        sa.CheckConstraint(
            "trigger IN ('scheduled', 'manual', 'backfill')",
            name=op.f("ck_pipeline_runs_run_trigger"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pipeline_runs")),
    )
    op.create_index(
        op.f("ix_pipeline_runs_started_at"), "pipeline_runs", ["started_at"], unique=False
    )
    op.create_table(
        "watch_topics",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("schedule", sa.String(length=100), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watch_topics")),
    )
    op.create_table(
        "missions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column(
            "scope", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("budget_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "planning",
                "running",
                "completed",
                "failed",
                "cancelled",
                "budget_exceeded",
                name="mission_status",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "cost_usd", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("watch_topic_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'planning', 'running', 'completed', 'failed', 'cancelled', 'budget_exceeded')",
            name=op.f("ck_missions_mission_status"),
        ),
        sa.CheckConstraint("budget_usd >= 0", name=op.f("ck_missions_budget_non_negative")),
        sa.CheckConstraint("max_steps > 0", name=op.f("ck_missions_max_steps_positive")),
        sa.ForeignKeyConstraint(
            ["watch_topic_id"],
            ["watch_topics.id"],
            name=op.f("fk_missions_watch_topic_id_watch_topics"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_missions")),
    )
    op.create_index(op.f("ix_missions_status"), "missions", ["status"], unique=False)
    op.create_index(
        op.f("ix_missions_watch_topic_id"), "missions", ["watch_topic_id"], unique=False
    )
    op.create_table(
        "reports",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "daily_brief",
                "battlecard",
                "digest",
                "mission_report",
                name="report_kind",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("entity_id", sa.BigInteger(), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "signal_ids", postgresql.ARRAY(sa.BigInteger()), server_default="{}", nullable=False
        ),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('daily_brief', 'battlecard', 'digest', 'mission_report')",
            name=op.f("ck_reports_report_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=op.f("fk_reports_entity_id_entities"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_reports_run_id_pipeline_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
    )
    op.create_index(
        "ix_reports_kind_entity_created",
        "reports",
        ["kind", "entity_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(op.f("ix_reports_run_id"), "reports", ["run_id"], unique=False)
    op.create_table(
        "sources",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "official",
                "press",
                "community",
                "github",
                name="source_kind",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "fetch_method",
            sa.Enum(
                "rss",
                "github",
                "page_diff",
                "web",
                name="fetch_method",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("trust_score", sa.Double(), nullable=False),
        sa.Column(
            "options", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "discovered_by",
            sa.Enum(
                "config",
                "radar",
                "mission",
                "analyst",
                name="discovered_by",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            server_default="config",
            nullable=False,
        ),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
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
            "discovered_by IN ('config', 'radar', 'mission', 'analyst')",
            name=op.f("ck_sources_discovered_by"),
        ),
        sa.CheckConstraint(
            "fetch_method IN ('rss', 'github', 'page_diff', 'web')",
            name=op.f("ck_sources_fetch_method"),
        ),
        sa.CheckConstraint(
            "kind IN ('official', 'press', 'community', 'github')",
            name=op.f("ck_sources_source_kind"),
        ),
        sa.CheckConstraint(
            "trust_score >= 0 AND trust_score <= 1", name=op.f("ck_sources_trust_score_range")
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=op.f("fk_sources_entity_id_entities"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint("url", name=op.f("uq_sources_url")),
    )
    op.create_index(
        "ix_sources_active_method", "sources", ["is_active", "fetch_method"], unique=False
    )
    op.create_index(op.f("ix_sources_entity_id"), "sources", ["entity_id"], unique=False)
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("task", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("tokens_in", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tokens_out", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "cost_usd", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False
        ),
        sa.Column("latency_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cache_hit", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("cache_key", sa.CHAR(length=64), nullable=True),
        sa.Column("success", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("mission_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_llm_calls_mission_id_missions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_llm_calls_run_id_pipeline_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
    )
    op.create_index(op.f("ix_llm_calls_cache_key"), "llm_calls", ["cache_key"], unique=False)
    op.create_index(op.f("ix_llm_calls_created_at"), "llm_calls", ["created_at"], unique=False)
    op.create_index(op.f("ix_llm_calls_mission_id"), "llm_calls", ["mission_id"], unique=False)
    op.create_index(op.f("ix_llm_calls_run_id"), "llm_calls", ["run_id"], unique=False)
    op.create_table(
        "mission_steps",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("mission_id", sa.BigInteger(), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "plan",
                "search",
                "fetch",
                "read",
                "analyze",
                "signal",
                "report",
                "error",
                name="mission_step_kind",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "input", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "output", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "cost_usd", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('plan', 'search', 'fetch', 'read', 'analyze', 'signal', 'report', 'error')",
            name=op.f("ck_mission_steps_mission_step_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_mission_steps_mission_id_missions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mission_steps")),
        sa.UniqueConstraint(
            "mission_id", "step_no", name=op.f("uq_mission_steps_mission_id_step_no")
        ),
    )
    op.create_table(
        "page_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_page_snapshots_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_page_snapshots")),
    )
    op.create_index(
        "ix_page_snapshots_source_fetched",
        "page_snapshots",
        ["source_id", sa.literal_column("fetched_at DESC")],
        unique=False,
    )
    op.create_table(
        "signals",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("mission_id", sa.BigInteger(), nullable=True),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "category",
            sa.Enum(
                "product_launch",
                "feature_update",
                "pricing_change",
                "partnership",
                "acquisition",
                "funding",
                "security_incident",
                "leadership_change",
                "customer_win",
                "open_source",
                "event",
                "analyst_report",
                "other",
                name="signal_category",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("why_jfrog_cares", sa.Text(), nullable=True),
        sa.Column("impact", sa.SmallInteger(), nullable=True),
        sa.Column("confidence", sa.Double(), nullable=True),
        sa.Column("evidence_quote", sa.Text(), nullable=True),
        sa.Column(
            "verification",
            sa.Enum(
                "pending",
                "supported",
                "partial",
                "unsupported",
                name="verification",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("verification_notes", sa.Text(), nullable=True),
        sa.Column("is_claim", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("dedup_cluster_id", sa.BigInteger(), nullable=True),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1536), nullable=True),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "new",
                "approved",
                "rejected",
                name="status",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            server_default="new",
            nullable=False,
        ),
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "search_tsv",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('english'::regconfig, coalesce(title, '') || ' ' || coalesce(summary, ''))",
                persisted=True,
            ),
            nullable=False,
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
            "category IN ('product_launch', 'feature_update', 'pricing_change', 'partnership', 'acquisition', 'funding', 'security_incident', 'leadership_change', 'customer_win', 'open_source', 'event', 'analyst_report', 'other')",
            name=op.f("ck_signals_signal_category"),
        ),
        sa.CheckConstraint(
            "status IN ('new', 'approved', 'rejected')", name=op.f("ck_signals_status")
        ),
        sa.CheckConstraint(
            "verification IN ('pending', 'supported', 'partial', 'unsupported')",
            name=op.f("ck_signals_verification"),
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name=op.f("ck_signals_confidence_range")
        ),
        sa.CheckConstraint("impact BETWEEN 1 AND 5", name=op.f("ck_signals_impact_range")),
        sa.ForeignKeyConstraint(
            ["dedup_cluster_id"],
            ["signals.id"],
            name=op.f("fk_signals_dedup_cluster_id_signals"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_signals_mission_id_missions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_signals_run_id_pipeline_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_signals_source_id_sources"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signals")),
        sa.UniqueConstraint("content_hash", name=op.f("uq_signals_content_hash")),
    )
    op.create_index(op.f("ix_signals_canonical_url"), "signals", ["canonical_url"], unique=False)
    op.create_index(
        op.f("ix_signals_dedup_cluster_id"), "signals", ["dedup_cluster_id"], unique=False
    )
    op.create_index(
        "ix_signals_embedding_hnsw",
        "signals",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(op.f("ix_signals_mission_id"), "signals", ["mission_id"], unique=False)
    op.create_index(op.f("ix_signals_published_at"), "signals", ["published_at"], unique=False)
    op.create_index(op.f("ix_signals_run_id"), "signals", ["run_id"], unique=False)
    op.create_index(
        "ix_signals_search_tsv", "signals", ["search_tsv"], unique=False, postgresql_using="gin"
    )
    op.create_index(op.f("ix_signals_source_id"), "signals", ["source_id"], unique=False)
    op.create_index(op.f("ix_signals_status"), "signals", ["status"], unique=False)
    op.create_table(
        "capability_matrix",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("capability_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "level",
            sa.Enum(
                "unknown",
                "none",
                "planned",
                "partial",
                "full",
                name="capability_level",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("evidence_signal_id", sa.BigInteger(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "level IN ('unknown', 'none', 'planned', 'partial', 'full')",
            name=op.f("ck_capability_matrix_capability_level"),
        ),
        sa.ForeignKeyConstraint(
            ["capability_id"],
            ["capabilities.id"],
            name=op.f("fk_capability_matrix_capability_id_capabilities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=op.f("fk_capability_matrix_entity_id_entities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_signal_id"],
            ["signals.id"],
            name=op.f("fk_capability_matrix_evidence_signal_id_signals"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capability_matrix")),
        sa.UniqueConstraint(
            "entity_id", "capability_id", name=op.f("uq_capability_matrix_entity_id_capability_id")
        ),
    )
    op.create_index(
        op.f("ix_capability_matrix_capability_id"),
        "capability_matrix",
        ["capability_id"],
        unique=False,
    )
    op.create_table(
        "insight_signals",
        sa.Column("insight_id", sa.BigInteger(), nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["insight_id"],
            ["insights.id"],
            name=op.f("fk_insight_signals_insight_id_insights"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
            name=op.f("fk_insight_signals_signal_id_signals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("insight_id", "signal_id", name=op.f("pk_insight_signals")),
    )
    op.create_index(
        op.f("ix_insight_signals_signal_id"), "insight_signals", ["signal_id"], unique=False
    )
    op.create_table(
        "signal_entities",
        sa.Column("signal_id", sa.BigInteger(), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=op.f("fk_signal_entities_entity_id_entities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
            name=op.f("fk_signal_entities_signal_id_signals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("signal_id", "entity_id", name=op.f("pk_signal_entities")),
    )
    op.create_index(
        op.f("ix_signal_entities_entity_id"), "signal_entities", ["entity_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_signal_entities_entity_id"), table_name="signal_entities")
    op.drop_table("signal_entities")
    op.drop_index(op.f("ix_insight_signals_signal_id"), table_name="insight_signals")
    op.drop_table("insight_signals")
    op.drop_index(op.f("ix_capability_matrix_capability_id"), table_name="capability_matrix")
    op.drop_table("capability_matrix")
    op.drop_index(op.f("ix_signals_status"), table_name="signals")
    op.drop_index(op.f("ix_signals_source_id"), table_name="signals")
    op.drop_index("ix_signals_search_tsv", table_name="signals", postgresql_using="gin")
    op.drop_index(op.f("ix_signals_run_id"), table_name="signals")
    op.drop_index(op.f("ix_signals_published_at"), table_name="signals")
    op.drop_index(op.f("ix_signals_mission_id"), table_name="signals")
    op.drop_index(
        "ix_signals_embedding_hnsw",
        table_name="signals",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_index(op.f("ix_signals_dedup_cluster_id"), table_name="signals")
    op.drop_index(op.f("ix_signals_canonical_url"), table_name="signals")
    op.drop_table("signals")
    op.drop_index("ix_page_snapshots_source_fetched", table_name="page_snapshots")
    op.drop_table("page_snapshots")
    op.drop_table("mission_steps")
    op.drop_index(op.f("ix_llm_calls_run_id"), table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_mission_id"), table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_created_at"), table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_cache_key"), table_name="llm_calls")
    op.drop_table("llm_calls")
    op.drop_index(op.f("ix_sources_entity_id"), table_name="sources")
    op.drop_index("ix_sources_active_method", table_name="sources")
    op.drop_table("sources")
    op.drop_index(op.f("ix_reports_run_id"), table_name="reports")
    op.drop_index("ix_reports_kind_entity_created", table_name="reports")
    op.drop_table("reports")
    op.drop_index(op.f("ix_missions_watch_topic_id"), table_name="missions")
    op.drop_index(op.f("ix_missions_status"), table_name="missions")
    op.drop_table("missions")
    op.drop_table("watch_topics")
    op.drop_index(op.f("ix_pipeline_runs_started_at"), table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
    op.drop_index(op.f("ix_insights_type"), table_name="insights")
    op.drop_index(op.f("ix_insights_status"), table_name="insights")
    op.drop_table("insights")
    op.drop_index("ix_feedback_target", table_name="feedback")
    op.drop_index(op.f("ix_feedback_created_at"), table_name="feedback")
    op.drop_table("feedback")
    op.drop_index(
        "uq_entities_single_self", table_name="entities", postgresql_where=sa.text("is_self")
    )
    op.drop_index(op.f("ix_entities_parent_id"), table_name="entities")
    op.drop_index("ix_entities_aliases", table_name="entities", postgresql_using="gin")
    op.drop_table("entities")
    op.drop_table("capabilities")
    op.execute("DROP EXTENSION IF EXISTS vector")
