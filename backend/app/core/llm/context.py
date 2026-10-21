"""Run / mission context for LLM calls, carried in a contextvar so callers don't pass it around.

with llm_context(run_id=run.id):
    complete(...)  # the llm_calls row gets run_id
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class LLMContext:
    run_id: int | None = None
    mission_id: int | None = None
    ask_turn_id: int | None = None


_EMPTY = LLMContext()
_current: ContextVar[LLMContext | None] = ContextVar("llm_context", default=None)


def current_context() -> LLMContext:
    return _current.get() or _EMPTY


@contextmanager
def llm_context(
    *, run_id: int | None = None, mission_id: int | None = None, ask_turn_id: int | None = None
) -> Iterator[None]:
    """Set run_id / mission_id / ask_turn_id for calls in this block; unset fields keep the outer
    value."""
    changes = {
        k: v
        for k, v in (("run_id", run_id), ("mission_id", mission_id), ("ask_turn_id", ask_turn_id))
        if v is not None
    }
    token = _current.set(replace(current_context(), **changes))
    try:
        yield
    finally:
        _current.reset(token)
