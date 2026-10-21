"""Research missions (S15): mission origin / company / capability / radar candidate / stop flag /
timeout, status awaiting_approval, step kind verify, mission_sources, mission_findings, feedback
target mission_finding.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUS = (
    "queued",
    "planning",
    "running",
    "completed",
    "failed",
    "cancelled",
    "budget_exceeded",
)
NEW_STATUS = (*OLD_STATUS[:2], "awaiting_approval", *OLD_STATUS[2:])
OLD_KIND = ("plan", "search", "fetch", "read", "analyze", "signal", "report", "error")
NEW_KIND = (*OLD_KIND[:6], "verify", *OLD_KIND[6:])
OLD_TARGET = ("signal", "insight", "capability_matrix", "mission", "radar_candidate", "ask_turn")
NEW_TARGET = (*OLD_TARGET, "mission_finding")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _replace_check(table: str, name: str, column: str, values: Sequence[str]) -> None:
    op.drop_constraint(op.f(name), table, type_="check")
    op.create_check_constraint(op.f(name), table, _in(column, values))


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def upgrade() -> None:
    _replace_check("missions", "ck_missions_mission_status", "status", NEW_STATUS)
    _replace_check("mission_steps", "ck_mission_steps_mission_step_kind", "kind", NEW_KIND)
    _replace_check("feedback", "ck_feedback_feedback_target", "target_type", NEW_TARGET)

    op.add_column(
        "missions",
        sa.Column(
            "origin_kind",
            _enum(
                "mission_origin", "manual", "signal", "radar_candidate", "capability", "ask_turn"
            ),
            server_default="manual",
            nullable=False,
        ),
    )
    op.add_column(
        "missions",
        sa.Column(
            "origin_ref",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.add_column("missions", sa.Column("entity_id", sa.BigInteger(), nullable=True))
    op.add_column("missions", sa.Column("capability_id", sa.BigInteger(), nullable=True))
    op.add_column("missions", sa.Column("radar_candidate_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "missions",
        sa.Column("stop_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "missions", sa.Column("timeout_s", sa.Integer(), server_default="600", nullable=False)
    )
    op.create_foreign_key(
        op.f("fk_missions_entity_id_entities"),
        "missions",
        "entities",
        ["entity_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_missions_capability_id_capabilities"),
        "missions",
        "capabilities",
        ["capability_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_missions_radar_candidate_id_radar_candidates"),
        "missions",
        "radar_candidates",
        ["radar_candidate_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_missions_entity_id"), "missions", ["entity_id"], unique=False)
    op.create_index(
        op.f("ix_missions_radar_candidate_id"), "missions", ["radar_candidate_id"], unique=False
    )

    op.create_table(
        "mission_sources",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("mission_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", _enum("mission_source_kind", "kb", "web"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_mission_sources_mission_id_missions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
            name=op.f("fk_mission_sources_signal_id_signals"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mission_sources")),
        sa.UniqueConstraint(
            "mission_id", "canonical_url", name=op.f("uq_mission_sources_mission_id_canonical_url")
        ),
    )
    op.create_index(
        op.f("ix_mission_sources_mission_id"), "mission_sources", ["mission_id"], unique=False
    )

    op.create_table(
        "mission_findings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("mission_id", sa.BigInteger(), nullable=False),
        sa.Column("sub_question_no", sa.SmallInteger(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("evidence_quote", sa.Text(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "confidence", _enum("finding_confidence", "high", "medium", "low"), nullable=False
        ),
        sa.Column("is_claim", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("impact", sa.SmallInteger(), nullable=True),
        sa.Column(
            "verification",
            _enum("verification", "pending", "supported", "partial", "unsupported"),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("verification_notes", sa.Text(), nullable=True),
        sa.Column(
            "status",
            _enum("finding_status", "new", "approved", "rejected", "dropped"),
            server_default="new",
            nullable=False,
        ),
        sa.Column("dropped_reason", sa.Text(), nullable=True),
        sa.Column(
            "meta", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("signal_id", sa.BigInteger(), nullable=True),
        sa.Column("reviewed_by", sa.String(length=100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_mission_findings_mission_id_missions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["mission_sources.id"],
            name=op.f("fk_mission_findings_source_id_mission_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
            name=op.f("fk_mission_findings_signal_id_signals"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mission_findings")),
    )
    op.create_index(
        op.f("ix_mission_findings_mission_id"), "mission_findings", ["mission_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("mission_findings")
    op.drop_table("mission_sources")
    op.drop_index(op.f("ix_missions_radar_candidate_id"), table_name="missions")
    op.drop_index(op.f("ix_missions_entity_id"), table_name="missions")
    for fk in (
        "fk_missions_radar_candidate_id_radar_candidates",
        "fk_missions_capability_id_capabilities",
        "fk_missions_entity_id_entities",
    ):
        op.drop_constraint(op.f(fk), "missions", type_="foreignkey")
    for col in (
        "timeout_s",
        "stop_requested",
        "radar_candidate_id",
        "capability_id",
        "entity_id",
        "origin_ref",
        "origin_kind",
    ):
        op.drop_column("missions", col)
    op.execute("DELETE FROM feedback WHERE target_type = 'mission_finding'")
    _replace_check("feedback", "ck_feedback_feedback_target", "target_type", OLD_TARGET)
    op.execute("UPDATE mission_steps SET kind = 'analyze' WHERE kind = 'verify'")
    _replace_check("mission_steps", "ck_mission_steps_mission_step_kind", "kind", OLD_KIND)
    op.execute("UPDATE missions SET status = 'cancelled' WHERE status = 'awaiting_approval'")
    _replace_check("missions", "ck_missions_mission_status", "status", OLD_STATUS)
