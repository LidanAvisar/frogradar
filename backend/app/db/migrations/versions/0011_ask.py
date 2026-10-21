"""Ask your intelligence (S13): ask_conversations, ask_turns, llm_calls.ask_turn_id, feedback on
answers (target ask_turn, reason missing_information).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TARGET = ("signal", "insight", "capability_matrix", "mission", "radar_candidate")
NEW_TARGET = (*OLD_TARGET, "ask_turn")
OLD_REASON = (
    "not_relevant", "wrong_entity", "wrong_category", "impact_too_high", "impact_too_low",
    "inaccurate_summary", "unsupported_claim", "duplicate", "outdated", "not_a_competitor",
    "too_small", "already_tracked", "other",
)  # fmt: skip
NEW_REASON = (*OLD_REASON[:-1], "missing_information", "other")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _replace_check(table: str, name: str, column: str, values: Sequence[str]) -> None:
    op.drop_constraint(op.f(name), table, type_="check")
    op.create_check_constraint(op.f(name), table, _in(column, values))


def upgrade() -> None:
    _replace_check("feedback", "ck_feedback_feedback_target", "target_type", NEW_TARGET)
    _replace_check("feedback", "ck_feedback_reason_code", "reason_code", NEW_REASON)
    op.create_table(
        "ask_conversations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ask_conversations")),
    )
    op.create_table(
        "ask_turns",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("standalone_question", sa.Text(), nullable=False),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("retrieved", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("answer", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("dropped", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("not_enough_evidence", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("prompt_versions", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=14, scale=10), nullable=True),
        sa.Column("cached", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["ask_conversations.id"],
            name=op.f("fk_ask_turns_conversation_id_ask_conversations"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ask_turns")),
    )  # fmt: skip
    op.create_index(
        "ix_ask_turns_conversation_position", "ask_turns", ["conversation_id", "position"]
    )
    op.add_column("llm_calls", sa.Column("ask_turn_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_llm_calls_ask_turn_id_ask_turns"), "llm_calls", "ask_turns",
        ["ask_turn_id"], ["id"], ondelete="SET NULL",
    )  # fmt: skip
    op.create_index(op.f("ix_llm_calls_ask_turn_id"), "llm_calls", ["ask_turn_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_llm_calls_ask_turn_id"), table_name="llm_calls")
    op.drop_constraint(op.f("fk_llm_calls_ask_turn_id_ask_turns"), "llm_calls", type_="foreignkey")
    op.drop_column("llm_calls", "ask_turn_id")
    op.drop_index("ix_ask_turns_conversation_position", table_name="ask_turns")
    op.drop_table("ask_turns")
    op.drop_table("ask_conversations")
    op.execute(
        "DELETE FROM feedback WHERE target_type = 'ask_turn' OR reason_code = 'missing_information'"
    )
    _replace_check("feedback", "ck_feedback_reason_code", "reason_code", OLD_REASON)
    _replace_check("feedback", "ck_feedback_feedback_target", "target_type", OLD_TARGET)
