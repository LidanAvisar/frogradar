"""Shared eval plumbing: run rows, a parallel map that never touches signals, cost estimates."""

import contextvars
import logging
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.llm import llm_context
from app.db.enums import RunStatus, RunTrigger
from app.db.models import LLMCall, PipelineRun

logger = logging.getLogger(__name__)


@contextmanager
def eval_run(factory: sessionmaker[Session], kind: str) -> Iterator[dict[str, Any]]:
    """A pipeline_runs row (trigger manual, counts.kind = eval_*) whose id tags every LLM call in
    the block. Yields the counts dict; cost is summed from llm_calls at the end."""
    with factory.begin() as s:
        run = PipelineRun(trigger=RunTrigger.MANUAL, counts={"kind": kind})
        s.add(run)
        s.flush()
        run_id = run.id
    counts: dict[str, Any] = {"kind": kind, "run_id": run_id}
    status, error = RunStatus.SUCCEEDED, None
    try:
        with llm_context(run_id=run_id):
            yield counts
    except BaseException as e:
        status, error = RunStatus.FAILED, f"{type(e).__name__}: {e}"[:500]
        raise
    finally:
        with factory.begin() as s:
            row = s.get_one(PipelineRun, run_id)
            cost = sum(
                (c or Decimal(0))
                for c in s.scalars(select(LLMCall.cost_usd).where(LLMCall.run_id == run_id))
            )
            row.counts = {k: v for k, v in counts.items() if _jsonable(v)}
            row.status, row.error, row.cost_usd = status, error, cost
            row.finished_at = datetime.now(UTC)


def _jsonable(v: Any) -> bool:
    return isinstance(v, (str, int, float, bool, list, dict, type(None)))


def parallel_map[T, R](
    fn: Callable[[T], R], items: Sequence[T], max_workers: int = 4
) -> list[R | Exception]:
    """fn over items in a thread pool (contextvars copied, so run_id reaches the gateway).
    A failing item yields its exception; results keep the input order."""

    def safe(item: T) -> R | Exception:
        try:
            return fn(item)
        except Exception as e:  # noqa: BLE001 - reported per item by the caller
            logger.warning("eval item failed: %s", e)
            return e

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="eval") as pool:
        futures = [pool.submit(contextvars.copy_context().run, safe, i) for i in items]
        return [f.result() for f in futures]


# ---------- cost estimates (printed before every paid step) ----------


@dataclass
class Estimate:
    calls: int = 0
    cached: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0
    by_model: dict[str, list[float]] = field(default_factory=dict)  # model -> [calls, usd]

    def add(self, model: str, tokens_in: int, tokens_out: int, cached: bool) -> None:
        self.calls += 1
        if cached:
            self.cached += 1
            return
        usd = price(model, tokens_in, tokens_out)
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.usd += usd
        entry = self.by_model.setdefault(model, [0, 0.0])
        entry[0] += 1
        entry[1] += usd

    def line(self, what: str) -> str:
        models = ", ".join(f"{m} {int(c)} calls ${u:.4f}" for m, (c, u) in self.by_model.items())
        return (
            f"{what}: {self.calls} calls ({self.cached} cached, free), "
            f"~{self.tokens_in:,} input + ~{self.tokens_out:,} output tokens, "
            f"est. ${self.usd:.4f}" + (f" [{models}]" if models else "")
        )


def price(model: str, tokens_in: int, tokens_out: int) -> float:
    import litellm

    info = litellm.model_cost.get(model.split("/", 1)[1]) or litellm.model_cost.get(model) or {}
    return tokens_in * (info.get("input_cost_per_token") or 0) + tokens_out * (
        info.get("output_cost_per_token") or 0
    )


def count_tokens(model: str, messages: Sequence[dict[str, Any]]) -> int:
    import litellm

    try:
        return int(litellm.token_counter(model=model, messages=list(messages)))
    except Exception:  # noqa: BLE001 - rough fallback: ~4 characters per token
        return sum(len(m["content"]) for m in messages) // 4


def probe(
    est: Estimate,
    task: str,
    messages: Sequence[dict[str, Any]],
    response_model: type[BaseModel],
    tokens_out: int,
    *,
    thinking: str | None = None,
) -> None:
    """Add one would-be call to the estimate (cache-aware; no call is made)."""
    from app.core.llm import get_gateway
    from app.core.llm.routing import resolve

    gw = get_gateway()
    model = resolve(task, gw.routing).model
    cached = gw.cached(task, messages, response_model, thinking=thinking)
    est.add(model, count_tokens(model, messages), tokens_out, cached)
