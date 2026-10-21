"""Ask your intelligence (S13): conversations and their turns.

A turn stores what was asked, the standalone query, the filters and documents retrieval used, the
answer as shown, and what the checks dropped (kept for audit, never returned by the API).
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at, pk, updated_at


class AskConversation(Base):
    __tablename__ = "ask_conversations"

    id: Mapped[int] = pk()
    title: Mapped[str] = mapped_column(String(200))  # the first question, cut
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class AskTurn(Base):
    __tablename__ = "ask_turns"
    __table_args__ = (Index("ix_ask_turns_conversation_position", "conversation_id", "position"),)

    id: Mapped[int] = pk()
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ask_conversations.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)  # 1, 2, ... within the conversation
    question: Mapped[str] = mapped_column(Text)
    standalone_question: Mapped[str] = mapped_column(Text)
    filters: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    # [{id, type, score, vector_rank, text_rank}] in rank order
    retrieved: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    answer: Mapped[dict[str, Any]] = mapped_column(server_default="{}")  # the shown part
    dropped: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")  # never returned
    not_enough_evidence: Mapped[bool] = mapped_column(server_default="false")
    prompt_versions: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 10))
    cached: Mapped[bool] = mapped_column(server_default="false")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
