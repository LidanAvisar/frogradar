"""What every pipeline step gets besides the DB session.

Steps are plain functions `step(session, ctx) -> counts`, so the CLI (app.pipeline.run) and,
later, LangGraph nodes can call them the same way. The step flushes; the caller commits.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.catalog import PipelineFile, load_llm_routing, load_pipeline
from app.core.llm import EmbeddingResult

EmbedFn = Callable[[Sequence[str]], EmbeddingResult]


@dataclass
class RunContext:
    run_id: int | None
    config: PipelineFile
    # The model the embed step writes and the dedup step compares within.
    embedding_model: str
    # Injected so tests use synthetic vectors; production uses the LLM gateway.
    embed: EmbedFn
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(cls, run_id: int | None) -> "RunContext":
        from app.core.llm import embed

        return cls(
            run_id=run_id,
            config=load_pipeline(),
            embedding_model=load_llm_routing().embedding.model,
            embed=lambda texts: embed(texts, operation="embed_signal"),
        )
