"""What every pipeline step gets besides the DB session.

Steps are plain functions `step(session, ctx) -> counts`, so the CLI (app.pipeline.run) and the
LangGraph nodes (app.pipeline.graph) call them the same way. The step flushes; the caller commits.
LLM steps select their work with `session`, then process each signal in its own transaction from
`ctx.session_factory`, so one failing signal never rolls back the others.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from app.core.catalog import PipelineFile, load_llm_routing, load_pipeline
from app.core.llm import EmbeddingResult

EmbedFn = Callable[[Sequence[str]], EmbeddingResult]


class CompleteFn(Protocol):
    """Signature of app.core.llm.complete (tests inject a scripted fake)."""

    def __call__[M: BaseModel](
        self,
        task: str,
        operation: str,
        messages: Sequence[dict[str, Any]],
        response_model: type[M],
        *,
        prompt_version: str | None = None,
    ) -> M: ...


@dataclass
class RunContext:
    run_id: int | None
    config: PipelineFile
    # The model the embed step writes and the dedup step compares within.
    embedding_model: str
    # Injected so tests use synthetic vectors / scripted outputs; production uses the gateway.
    embed: EmbedFn
    complete: CompleteFn | None = None
    session_factory: sessionmaker[Session] | None = None
    # Stages to redo even if already done with the current prompt version: triage/enrich/verify.
    force: frozenset[str] = frozenset()
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        run_id: int | None,
        *,
        session_factory: sessionmaker[Session] | None = None,
        force: frozenset[str] = frozenset(),
    ) -> "RunContext":
        from app.core.llm import complete, embed

        if session_factory is None:
            from app.db.session import get_engine

            session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
        return cls(
            run_id=run_id,
            config=load_pipeline(),
            embedding_model=load_llm_routing().embedding.model,
            embed=lambda texts: embed(texts, operation="embed_signal"),
            complete=complete,
            session_factory=session_factory,
            force=force,
        )

    def require_llm(self) -> tuple[CompleteFn, sessionmaker[Session]]:
        if self.complete is None or self.session_factory is None:
            raise RuntimeError("LLM steps need ctx.complete and ctx.session_factory")
        return self.complete, self.session_factory
