"""ask_conversations.source: ui (asked by analysts) or eval (make eval-ask), so eval conversations stay in
the DB for the eval history but are hidden from Recent questions.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ask_conversations",
        sa.Column(
            "source",
            sa.Enum(
                "ui",
                "eval",
                name="ask_source",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="ui",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("ask_conversations", "source")
