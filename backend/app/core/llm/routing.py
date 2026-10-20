"""Resolve a task tier (bulk / synthesis / verify) to a model via config/llm_routing.yaml.

Env overrides (LLM_MODEL_BULK, …, EMBEDDING_MODEL) are applied by app.core.catalog.load_llm_routing.
"""

from typing import Literal, get_args

from app.core.catalog import EmbeddingRoute, LLMRoutingFile, TaskRoute

Task = Literal["bulk", "synthesis", "verify"]
TASKS: tuple[str, ...] = get_args(Task)


def resolve(task: str, routing: LLMRoutingFile) -> TaskRoute:
    if task not in TASKS:
        raise ValueError(f"unknown LLM task {task!r}; expected one of {TASKS}")
    return getattr(routing.tasks, task)


def embedding_route(routing: LLMRoutingFile) -> EmbeddingRoute:
    return routing.embedding
