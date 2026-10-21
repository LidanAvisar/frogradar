"""entities.discontinued_on: products that reached end of life (e.g. JFrog Pipelines, 2026-05-01).

Prompts list discontinued JFrog products separately and must never recommend them; the UI does not
highlight them as JFrog products.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("entities", sa.Column("discontinued_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("entities", "discontinued_on")
