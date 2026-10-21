"""llm_calls.tokens_reasoning: output tokens spent on reasoning/thinking (NULL = unknown).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("llm_calls", sa.Column("tokens_reasoning", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_calls", "tokens_reasoning")
