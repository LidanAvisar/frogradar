"""Collectors: signals.meta, unique (source_id, canonical_url), HTTP validators on sources.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "signals",
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    # A signal's identity within a source is its URL; NULL source_id (mission findings) never clash.
    op.create_index(
        "uq_signals_source_id_canonical_url",
        "signals",
        ["source_id", "canonical_url"],
        unique=True,
    )
    op.add_column("sources", sa.Column("http_etag", sa.Text(), nullable=True))
    op.add_column("sources", sa.Column("http_last_modified", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "http_last_modified")
    op.drop_column("sources", "http_etag")
    op.drop_index("uq_signals_source_id_canonical_url", table_name="signals")
    op.drop_column("signals", "meta")
