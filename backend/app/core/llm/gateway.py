"""LLMGateway: the one path every LLM and embedding call in FrogRadar goes through.

Per call: route the task to a model → cache lookup → budget check →
provider call with retries → validate against the Pydantic schema (retrying with the error) →
llm_calls row for every attempt → cache the validated result.

Ledger and cache writes use their own short sessions, so they are committed even if the caller's
transaction rolls back (failed calls still cost money and must stay visible).
"""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from app.core.catalog import LLMRoutingFile, load_llm_routing
from app.core.config import Settings, get_settings
from app.core.llm import cache, ledger, structured
from app.core.llm.context import current_context
from app.core.llm.errors import LLMError, StructuredOutputError, scrub
from app.core.llm.ledger import CallRecord
from app.core.llm.pricing import compute_cost
from app.core.llm.provider import LiteLLMProvider, describe
from app.core.llm.routing import embedding_route, resolve

logger = logging.getLogger("app.core.llm")

EMBED_BATCH_SIZE = 256
_DEBUG_CHARS = 500

Message = dict[str, Any]


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    dimensions: int


class LLMGateway:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        routing: LLMRoutingFile | None = None,
        session_factory: Callable[[], Session] | None = None,
        provider: LiteLLMProvider | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._routing = routing
        if session_factory is None:
            from app.db.session import get_engine

            session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
        self._session_factory = session_factory
        self.provider = provider or LiteLLMProvider(self.settings)

    @property
    def routing(self) -> LLMRoutingFile:
        # Not cached here: load_llm_routing is cached itself and applies env overrides.
        return self._routing or load_llm_routing()

    # ---------- completions ----------

    def complete[M: BaseModel](
        self,
        task: str,
        operation: str,
        messages: Sequence[Message],
        response_model: type[M],
        *,
        prompt_version: str | None = None,
        use_cache: bool = True,
        thinking: str | None = None,
    ) -> M:
        """Run a chat completion and return a validated `response_model` instance.

        thinking: overrides the task's configured thinking level for this call (eval A/B only);
        it is part of the cache key like the configured value."""
        route = resolve(task, self.routing)
        if thinking is not None and thinking != route.thinking:
            route = route.model_copy(update={"thinking": thinking})
        ctx = current_context()
        messages = list(messages)
        schema = response_model.model_json_schema()
        key = cache.completion_key(
            model=route.model,
            max_tokens=route.max_tokens,
            temperature=route.temperature,
            messages=messages,
            schema=schema,
            thinking=route.thinking,
        )

        def record(**fields: Any) -> CallRecord:
            return CallRecord(
                task=task,
                operation=operation,
                model=route.model,
                cache_key=key,
                prompt_version=prompt_version,
                run_id=ctx.run_id,
                mission_id=ctx.mission_id,
                ask_turn_id=ctx.ask_turn_id,
                **fields,
            )

        _debug_prompt(operation, messages)

        # 1. Cache.
        if use_cache:
            start = time.perf_counter()
            with self._session_factory() as s, s.begin():
                hit = cache.get_many(s, [key]).get(key)
                obj = _validate_cached(hit.response, response_model) if hit else None
                if obj is not None:
                    cache.mark_hits(s, [key])
                    rec = record(
                        tokens_in=hit.tokens_in,
                        tokens_out=hit.tokens_out,
                        cost_usd=Decimal(0),
                        latency_ms=int((time.perf_counter() - start) * 1000),
                        cache_hit=True,
                    )
                    ledger.add(s, rec)
            if obj is not None:
                _log(rec)
                return obj

        use_schema = structured.supports_schema(route.model)
        fmt = structured.response_format(response_model) if use_schema else None
        convo = (
            messages if use_schema else structured.with_schema_instruction(messages, response_model)
        )

        last_error = ""
        for attempt in range(1, structured.MAX_VALIDATION_RETRIES + 2):
            self._check_budgets(ctx.mission_id)

            def on_error(exc: Exception, latency_ms: int, attempt: int = attempt) -> None:
                rec = record(
                    attempt=attempt, latency_ms=latency_ms, success=False, error=describe(exc)
                )
                self._write(rec)

            result = self.provider.complete(
                model=route.model,
                messages=convo,
                max_tokens=route.max_tokens,
                temperature=route.temperature,
                response_format=fmt,
                on_error=on_error,
                thinking=route.thinking,
            )
            cost = compute_cost(
                model=route.model,
                raw_response=result.raw,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                pricing=self.routing.pricing,
            )
            obj, error = structured.parse(result.text, response_model)
            rec = record(
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                tokens_reasoning=result.tokens_reasoning,
                cost_usd=cost,
                latency_ms=result.latency_ms,
                attempt=attempt,
                success=obj is not None,
                error=None if obj is not None else scrub(f"validation: {error}"),
            )
            logger.debug("llm %s output: %s", operation, result.text[:_DEBUG_CHARS])

            if obj is not None:
                with self._session_factory() as s, s.begin():
                    ledger.add(s, rec)
                    if use_cache:
                        cache.put(
                            s,
                            key=key,
                            kind="completion",
                            model=route.model,
                            operation=operation,
                            prompt_version=prompt_version,
                            response=obj.model_dump(mode="json"),
                            tokens_in=result.tokens_in,
                            tokens_out=result.tokens_out,
                            cost_usd=cost,
                        )
                _log(rec)
                return obj

            if result.finish_reason == "length":
                # Cut off at max_tokens: a retry with the same limit fails the same way.
                rec.error = scrub(
                    f"truncated at max_tokens={route.max_tokens} ({result.tokens_out} tokens "
                    f"out); raise max_tokens for task '{task}'. {error}"
                )[:1000]
                self._write(rec)
                _log(rec)
                raise StructuredOutputError(f"{operation}: {rec.error}")
            self._write(rec)
            _log(rec)
            last_error = error or ""
            convo = [*convo, *structured.retry_messages(result.text, last_error)]

        raise StructuredOutputError(
            f"{operation}: output failed {response_model.__name__} validation "
            f"{structured.MAX_VALIDATION_RETRIES + 1} times; last error: {scrub(last_error)}"
        )

    def cached(
        self,
        task: str,
        messages: Sequence[Message],
        response_model: type[BaseModel],
        *,
        thinking: str | None = None,
    ) -> bool:
        """Would complete() be answered from the cache? (cost estimates; no call, no ledger row)"""
        route = resolve(task, self.routing)
        key = cache.completion_key(
            model=route.model,
            max_tokens=route.max_tokens,
            temperature=route.temperature,
            messages=list(messages),
            schema=response_model.model_json_schema(),
            thinking=thinking if thinking is not None else route.thinking,
        )
        with self._session_factory() as s:
            hit = cache.get_many(s, [key]).get(key)
            return hit is not None and _validate_cached(hit.response, response_model) is not None

    # ---------- embeddings ----------

    def embed(
        self, texts: Sequence[str], *, operation: str = "embed", use_cache: bool = True
    ) -> EmbeddingResult:
        """Embed texts in batches. Each text is cached separately, so only misses are sent."""
        route = embedding_route(self.routing)
        texts = list(texts)
        if not texts:
            return EmbeddingResult([], route.model, route.dimensions)
        if any(not t.strip() for t in texts):
            raise ValueError("embed() got an empty text")
        ctx = current_context()
        keys = [
            cache.embedding_key(model=route.model, dimensions=route.dimensions, text=t)
            for t in texts
        ]
        vectors: dict[str, list[float]] = {}

        def record(**fields: Any) -> CallRecord:
            return CallRecord(
                task="embedding",
                operation=operation,
                model=route.model,
                run_id=ctx.run_id,
                mission_id=ctx.mission_id,
                ask_turn_id=ctx.ask_turn_id,
                **fields,
            )

        # 1. Cache.
        if use_cache:
            start = time.perf_counter()
            with self._session_factory() as s, s.begin():
                hits = cache.get_many(s, list(dict.fromkeys(keys)))
                hits = {k: h for k, h in hits.items() if len(h.response) == route.dimensions}
                if hits:
                    cache.mark_hits(s, list(hits))
                    vectors.update({k: h.response for k, h in hits.items()})
                    rec = record(
                        tokens_in=sum(h.tokens_in for h in hits.values()),
                        cost_usd=Decimal(0),
                        latency_ms=int((time.perf_counter() - start) * 1000),
                        cache_hit=True,
                    )
                    ledger.add(s, rec)
            if hits:
                _log(rec)

        # Unique texts still missing, in first-seen order.
        missing = {k: t for k, t in zip(keys, texts, strict=True) if k not in vectors}

        # 2. Provider, in batches.
        items = list(missing.items())
        for i in range(0, len(items), EMBED_BATCH_SIZE):
            batch = items[i : i + EMBED_BATCH_SIZE]
            self._check_budgets(ctx.mission_id)
            result = self.provider.embed(
                model=route.model,
                texts=[t for _, t in batch],
                dimensions=route.dimensions,
                on_error=lambda exc, ms: self._write(
                    record(latency_ms=ms, success=False, error=describe(exc))
                ),
            )
            cost = compute_cost(
                model=route.model,
                raw_response=result.raw,
                tokens_in=result.tokens_in,
                tokens_out=0,
                pricing=self.routing.pricing,
                call_type="embedding",
            )
            rec = record(tokens_in=result.tokens_in, cost_usd=cost, latency_ms=result.latency_ms)
            bad = {len(v) for v in result.vectors if len(v) != route.dimensions}
            if len(result.vectors) != len(batch) or bad:
                rec.success = False
                rec.error = (
                    f"expected {len(batch)} vectors of {route.dimensions} dims, got "
                    f"{len(result.vectors)} (bad sizes: {sorted(bad)})"
                )
                self._write(rec)
                raise LLMError(f"embedding {route.model}: {rec.error}")

            # Usage is per batch; split it evenly to record an approximate per-text share.
            share = result.tokens_in // len(batch)
            share_cost = None if cost is None else cost / len(batch)
            with self._session_factory() as s, s.begin():
                ledger.add(s, rec)
                for (key, _), vec in zip(batch, result.vectors, strict=True):
                    vectors[key] = vec
                    if use_cache:
                        cache.put(
                            s,
                            key=key,
                            kind="embedding",
                            model=route.model,
                            operation=operation,
                            prompt_version=None,
                            response=vec,
                            tokens_in=share,
                            tokens_out=0,
                            cost_usd=share_cost,
                        )
            _log(rec)

        return EmbeddingResult([vectors[k] for k in keys], route.model, route.dimensions)

    # ---------- helpers ----------

    def _write(self, rec: CallRecord) -> None:
        with self._session_factory() as s, s.begin():
            ledger.add(s, rec)

    def _check_budgets(self, mission_id: int | None) -> None:
        with self._session_factory() as s:
            ledger.check_budgets(
                s, daily_budget_usd=self.settings.llm_daily_budget_usd, mission_id=mission_id
            )


def _validate_cached[M: BaseModel](data: Any, model_cls: type[M]) -> M | None:
    try:
        return model_cls.model_validate(data)
    except ValueError:
        logger.warning("cached %s no longer validates; treating as a miss", model_cls.__name__)
        return None


def _debug_prompt(operation: str, messages: list[Message]) -> None:
    if logger.isEnabledFor(logging.DEBUG):
        text = " | ".join(f"{m.get('role')}: {m.get('content')}" for m in messages)
        logger.debug("llm %s prompt: %s", operation, text[:_DEBUG_CHARS])


def _log(rec: CallRecord) -> None:
    cost = "unknown" if rec.cost_usd is None else f"${rec.cost_usd:.6f}"
    logger.info(
        "llm %s task=%s model=%s attempt=%d cache_hit=%s ok=%s tokens=%d/%d cost=%s %dms",
        rec.operation, rec.task, rec.model, rec.attempt, rec.cache_hit, rec.success,
        rec.tokens_in, rec.tokens_out, cost, rec.latency_ms,
    )  # fmt: skip


@lru_cache
def get_gateway() -> LLMGateway:
    return LLMGateway()
