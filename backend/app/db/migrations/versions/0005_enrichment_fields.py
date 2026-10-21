"""Enrichment: signals.relevant / evidence_quote_verified / enrich_prompt_version, signal_capabilities.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("relevant", sa.Boolean(), nullable=True))
    op.add_column("signals", sa.Column("evidence_quote_verified", sa.Boolean(), nullable=True))
    op.add_column(
        "signals", sa.Column("enrich_prompt_version", sa.String(length=64), nullable=True)
    )
    op.create_index(
        "ix_signals_relevant",
        "signals",
        ["relevant"],
        postgresql_where=sa.text("relevant IS NOT NULL"),
    )
    op.create_table(
        "signal_capabilities",
        sa.Column("signal_id", sa.BigInteger(), nullable=False),
        sa.Column("capability_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["capability_id"],
            ["capabilities.id"],
            name=op.f("fk_signal_capabilities_capability_id_capabilities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
            name=op.f("fk_signal_capabilities_signal_id_signals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("signal_id", "capability_id", name=op.f("pk_signal_capabilities")),
    )
    op.create_index(
        op.f("ix_signal_capabilities_capability_id"),
        "signal_capabilities",
        ["capability_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_signal_capabilities_capability_id"), table_name="signal_capabilities")
    op.drop_table("signal_capabilities")
    op.drop_index("ix_signals_relevant", table_name="signals")
    op.drop_column("signals", "enrich_prompt_version")
    op.drop_column("signals", "evidence_quote_verified")
    op.drop_column("signals", "relevant")
