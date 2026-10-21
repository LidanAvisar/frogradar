"""page_snapshots.captured_at (when the content was live) and meta (origin, wayback timestamp).

fetched_at = when we stored the row; captured_at = when the page looked like this (equal for live
fetches, the archive timestamp for Wayback snapshots). Diffs are ordered by captured_at.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "page_snapshots", sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("UPDATE page_snapshots SET captured_at = fetched_at")
    op.alter_column("page_snapshots", "captured_at", nullable=False)
    op.add_column(
        "page_snapshots",
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_page_snapshots_source_captured",
        "page_snapshots",
        ["source_id", sa.text("captured_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_page_snapshots_source_captured", table_name="page_snapshots")
    op.drop_column("page_snapshots", "meta")
    op.drop_column("page_snapshots", "captured_at")
