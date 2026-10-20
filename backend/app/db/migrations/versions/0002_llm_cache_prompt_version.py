"""LLM gateway: llm_cache table; prompt_version, attempt and nullable, finer cost on llm_calls.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_cache",
        sa.Column("cache_key", sa.CHAR(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tokens_in", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tokens_out", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=14, scale=10), nullable=True),
        sa.Column("hit_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("cache_key", name=op.f("pk_llm_cache")),
    )
    op.add_column("llm_calls", sa.Column("prompt_version", sa.String(length=64), nullable=True))
    op.add_column(
        "llm_calls", sa.Column("attempt", sa.SmallInteger(), server_default="1", nullable=False)
    )
    op.create_index(
        op.f("ix_llm_calls_prompt_version"), "llm_calls", ["prompt_version"], unique=False
    )
    # NULL = cost unknown, so unknown prices are never recorded as a silent 0.
    # 10 decimals: one short embedding call costs ~1e-7 USD and would round to 0 at 6 decimals.
    op.alter_column(
        "llm_calls",
        "cost_usd",
        existing_type=sa.Numeric(precision=12, scale=6),
        type_=sa.Numeric(precision=14, scale=10),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("UPDATE llm_calls SET cost_usd = 0 WHERE cost_usd IS NULL")
    op.alter_column(
        "llm_calls",
        "cost_usd",
        existing_type=sa.Numeric(precision=14, scale=10),
        type_=sa.Numeric(precision=12, scale=6),
        nullable=False,
    )
    op.drop_index(op.f("ix_llm_calls_prompt_version"), table_name="llm_calls")
    op.drop_column("llm_calls", "attempt")
    op.drop_column("llm_calls", "prompt_version")
    op.drop_table("llm_cache")
