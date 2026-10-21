"""signals.takeaway (one-line point for JFrog) and capability_matrix level history.

takeaway is written by enrichment (enrich_signal.v3 / enrich_page_diff.v4). previous_level and
level_changed_at let the battlecard API flag cells whose level changed recently.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEVELS = ("unknown", "none", "planned", "partial", "full")


def upgrade() -> None:
    op.add_column("signals", sa.Column("takeaway", sa.Text(), nullable=True))
    op.add_column(
        "capability_matrix",
        sa.Column(
            "previous_level",
            sa.Enum(
                *LEVELS,
                name="previous_capability_level",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        op.f("ck_capability_matrix_previous_capability_level"),
        "capability_matrix",
        "previous_level IN ('unknown', 'none', 'planned', 'partial', 'full')",
    )
    op.add_column(
        "capability_matrix",
        sa.Column("level_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("capability_matrix", "level_changed_at")
    op.drop_constraint(
        op.f("ck_capability_matrix_previous_capability_level"), "capability_matrix", type_="check"
    )
    op.drop_column("capability_matrix", "previous_level")
    op.drop_column("signals", "takeaway")
