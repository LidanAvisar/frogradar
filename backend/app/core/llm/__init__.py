"""LLM gateway: every LLM and embedding call in FrogRadar goes through here.

from app.core.llm import complete, embed, llm_context, load_prompt

prompt = load_prompt("enrich_signal")
with llm_context(run_id=run.id):
    result = complete("bulk", "enrich_signal", prompt.messages(text=...), EnrichedSignal,
                      prompt_version=prompt.id)
"""

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from app.core.llm.context import LLMContext, current_context, llm_context
from app.core.llm.errors import (
    BudgetExceeded,
    DemoCacheMiss,
    LLMError,
    MissionBudgetExceeded,
    ProviderError,
    StructuredOutputError,
)
from app.core.llm.gateway import EmbeddingResult, LLMGateway, get_gateway
from app.core.llm.prompts import Prompt, PromptError, load_prompt
from app.core.llm.routing import TASKS, Task


def complete[M: BaseModel](
    task: str,
    operation: str,
    messages: Sequence[dict[str, Any]],
    response_model: type[M],
    *,
    prompt_version: str | None = None,
    use_cache: bool = True,
    thinking: str | None = None,
) -> M:
    """Chat completion validated into `response_model`. task: bulk / synthesis / verify.
    thinking overrides the task's configured level for this call (eval A/B)."""
    return get_gateway().complete(
        task,
        operation,
        messages,
        response_model,
        prompt_version=prompt_version,
        use_cache=use_cache,
        thinking=thinking,
    )


def embed(
    texts: Sequence[str], *, operation: str = "embed", use_cache: bool = True
) -> EmbeddingResult:
    """Embeddings with the configured embedding model; returns vectors + model id."""
    return get_gateway().embed(texts, operation=operation, use_cache=use_cache)


__all__ = [
    "TASKS",
    "BudgetExceeded",
    "DemoCacheMiss",
    "EmbeddingResult",
    "LLMContext",
    "LLMError",
    "LLMGateway",
    "MissionBudgetExceeded",
    "Prompt",
    "PromptError",
    "ProviderError",
    "StructuredOutputError",
    "Task",
    "complete",
    "current_context",
    "embed",
    "get_gateway",
    "llm_context",
    "load_prompt",
]
