"""ask_conversations.source: mcp (questions asked through the MCP server's ask tool, S16).

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-22

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = "ck_ask_conversations_ask_source"


def upgrade() -> None:
    op.drop_constraint(op.f(NAME), "ask_conversations", type_="check")
    op.create_check_constraint(op.f(NAME), "ask_conversations", "source IN ('ui', 'eval', 'mcp')")


def downgrade() -> None:
    op.execute("UPDATE ask_conversations SET source = 'eval' WHERE source = 'mcp'")
    op.drop_constraint(op.f(NAME), "ask_conversations", type_="check")
    op.create_check_constraint(op.f(NAME), "ask_conversations", "source IN ('ui', 'eval')")
