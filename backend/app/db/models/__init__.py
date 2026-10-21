"""Import every model so Base.metadata is complete (Alembic and tests rely on this)."""

from app.db.base import Base
from app.db.models.catalog import Capability, CapabilityMatrix, Entity, Source
from app.db.models.intel import (
    EMBEDDING_DIM,
    Feedback,
    Insight,
    PageSnapshot,
    Report,
    Signal,
    insight_signals,
    signal_capabilities,
    signal_entities,
)
from app.db.models.ops import LLMCache, LLMCall, Mission, MissionStep, PipelineRun, WatchTopic

__all__ = [
    "EMBEDDING_DIM",
    "Base",
    "Capability",
    "CapabilityMatrix",
    "Entity",
    "Feedback",
    "Insight",
    "LLMCache",
    "LLMCall",
    "Mission",
    "MissionStep",
    "PageSnapshot",
    "PipelineRun",
    "Report",
    "Signal",
    "Source",
    "WatchTopic",
    "insight_signals",
    "signal_capabilities",
    "signal_entities",
]
