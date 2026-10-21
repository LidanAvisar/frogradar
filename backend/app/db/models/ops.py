"""How the system runs: research missions, watch topics, pipeline runs, LLM call log."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy import text as sql
from sqlalchemy.dialects.postgresql import CHAR, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at, enum_type, pk
from app.db.enums import MissionStatus, MissionStepKind, RunStatus, RunTrigger


class WatchTopic(Base):
    """A standing query the analyst wants watched; may spawn scheduled missions."""

    __tablename__ = "watch_topics"

    id: Mapped[int] = pk()
    query: Mapped[str]
    created_by: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(server_default=sql("true"))
    schedule: Mapped[str | None] = mapped_column(String(100))  # cron expression
    last_run_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at()


class Mission(Base):
    """A research task an analyst assigns to the agent, with a hard budget."""

    __tablename__ = "missions"
    __table_args__ = (
        CheckConstraint("max_steps > 0", name="max_steps_positive"),
        CheckConstraint("budget_usd >= 0", name="budget_non_negative"),
    )

    id: Mapped[int] = pk()
    title: Mapped[str]
    brief: Mapped[str]
    scope: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    budget_usd: Mapped[Decimal]
    max_steps: Mapped[int] = mapped_column(Integer)
    status: Mapped[MissionStatus] = mapped_column(
        enum_type(MissionStatus, "mission_status"),
        server_default=MissionStatus.QUEUED.value,
        index=True,
    )
    plan: Mapped[dict[str, Any] | None]
    report: Mapped[dict[str, Any] | None]
    cost_usd: Mapped[Decimal] = mapped_column(server_default="0")
    error: Mapped[str | None]
    watch_topic_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("watch_topics.id", ondelete="SET NULL"), index=True
    )
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = created_at()
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    steps: Mapped[list["MissionStep"]] = relationship(
        back_populates="mission", order_by="MissionStep.step_no", cascade="all, delete-orphan"
    )


class MissionStep(Base):
    __tablename__ = "mission_steps"
    __table_args__ = (UniqueConstraint("mission_id", "step_no"),)

    id: Mapped[int] = pk()
    mission_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("missions.id", ondelete="CASCADE")
    )
    step_no: Mapped[int] = mapped_column(Integer)
    kind: Mapped[MissionStepKind] = mapped_column(enum_type(MissionStepKind, "mission_step_kind"))
    summary: Mapped[str]
    input: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    output: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    cost_usd: Mapped[Decimal] = mapped_column(server_default="0")
    created_at: Mapped[datetime] = created_at()

    mission: Mapped[Mission] = relationship(back_populates="steps")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = pk()
    trigger: Mapped[RunTrigger] = mapped_column(enum_type(RunTrigger, "run_trigger"))
    started_at: Mapped[datetime] = mapped_column(server_default=sql("now()"), index=True)
    finished_at: Mapped[datetime | None]
    status: Mapped[RunStatus] = mapped_column(
        enum_type(RunStatus, "run_status"), server_default=RunStatus.RUNNING.value
    )
    counts: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    cost_usd: Mapped[Decimal] = mapped_column(server_default="0")
    error: Mapped[str | None]


class LLMCall(Base):
    """One row per LLM/embedding call (including cache hits) for cost and latency tracking."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = pk()
    task: Mapped[str] = mapped_column(String(32))  # routing tier: bulk/synthesis/verify/embedding
    operation: Mapped[str] = mapped_column(String(64))  # e.g. enrich_signal
    model: Mapped[str] = mapped_column(String(100))
    tokens_in: Mapped[int] = mapped_column(Integer, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, server_default="0")
    # Part of tokens_out spent on reasoning/thinking, from the provider's usage (NULL = unknown).
    tokens_reasoning: Mapped[int | None] = mapped_column(Integer)
    # NULL = cost unknown (neither LiteLLM nor llm_routing.yaml pricing knows the model).
    # No server default: the gateway always sets it, and a default would turn NULL into 0.
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 10))
    latency_ms: Mapped[int] = mapped_column(Integer, server_default="0")
    cache_hit: Mapped[bool] = mapped_column(server_default=sql("false"))
    cache_key: Mapped[str | None] = mapped_column(CHAR(64), index=True)
    # Prompt file id, e.g. "enrich_signal.v1", so eval results can be tied to prompt versions.
    prompt_version: Mapped[str | None] = mapped_column(String(64), index=True)
    # 1 = first try; 2-3 = structured-output retries after a validation failure.
    attempt: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    success: Mapped[bool] = mapped_column(server_default=sql("true"))
    error: Mapped[str | None]
    run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pipeline_runs.id", ondelete="SET NULL"), index=True
    )
    mission_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("missions.id", ondelete="SET NULL"), index=True
    )
    ask_turn_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ask_turns.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=sql("now()"), index=True)


class LLMCache(Base):
    """Validated LLM outputs and embeddings by content hash. Also the source for demo mode."""

    __tablename__ = "llm_cache"

    cache_key: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # completion / embedding
    model: Mapped[str] = mapped_column(String(100))
    operation: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    # Validated object (completion) or vector (embedding).
    response: Mapped[Any] = mapped_column(JSONB)
    tokens_in: Mapped[int] = mapped_column(Integer, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 10))  # original call's cost
    hit_count: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = created_at()
    last_hit_at: Mapped[datetime | None]
