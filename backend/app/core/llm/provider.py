"""The only module that talks to LiteLLM: timeouts, retries with backoff, error classification.

Tests patch `litellm.completion` / `litellm.embedding`; this module looks them up on every call.
"""

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import litellm
from litellm import exceptions as lx

from app.core.config import Settings
from app.core.llm.errors import DemoCacheMiss, ProviderError, scrub

# Keep LiteLLM quiet: no debug banners, no request/header logging, no retries of its own.
litellm.suppress_debug_info = True
if hasattr(litellm, "telemetry"):
    litellm.telemetry = False
for _name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "litellm"):
    logging.getLogger(_name).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Worth retrying: rate limits, timeouts, connection problems, provider-side 5xx.
RETRYABLE: tuple[type[Exception], ...] = (
    lx.RateLimitError,
    lx.Timeout,
    lx.APIConnectionError,
    lx.InternalServerError,
    lx.ServiceUnavailableError,
    lx.BadGatewayError,
)
MAX_BACKOFF_S = 30.0

# Called after every failed attempt (retried or not) with the exception and its latency.
OnError = Callable[[Exception, int], None]


@dataclass
class CompletionResult:
    text: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    raw: Any  # litellm ModelResponse, for cost calculation


@dataclass
class EmbeddingBatch:
    vectors: list[list[float]]
    tokens_in: int
    latency_ms: int
    raw: Any


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, RETRYABLE):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status >= 500


def _retry_after_s(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(exc, "headers", None) or getattr(response, "headers", None)
    if not headers:
        return None
    try:
        value = headers.get("retry-after") or headers.get("Retry-After")
        return float(value) if value is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def describe(exc: Exception) -> str:
    """Short, key-free description of an exception for logs and llm_calls.error."""
    return scrub(f"{type(exc).__name__}: {exc}")


class LiteLLMProvider:
    def __init__(self, settings: Settings, sleep: Callable[[float], None] = time.sleep) -> None:
        self.settings = settings
        self._sleep = sleep

    def _refuse_in_demo_mode(self, what: str) -> None:
        # Defense in depth: the gateway already serves demo mode from the cache only.
        if self.settings.demo_mode:
            raise DemoCacheMiss(f"DEMO_MODE is on; refusing to call the provider for {what}")

    def _with_retries(self, what: str, call: Callable[[], Any], on_error: OnError | None) -> Any:
        attempts = self.settings.llm_max_retries + 1
        for attempt in range(1, attempts + 1):
            start = time.perf_counter()
            try:
                return call()
            except Exception as exc:  # noqa: BLE001 - classify everything the provider raises
                latency_ms = int((time.perf_counter() - start) * 1000)
                if on_error:
                    on_error(exc, latency_ms)
                retryable = _is_retryable(exc)
                if not retryable or attempt == attempts:
                    suffix = f" after {attempt} attempts" if retryable else ""
                    raise ProviderError(
                        f"{what} failed{suffix}: {describe(exc)}", retryable=retryable
                    ) from None
                base = self.settings.llm_backoff_base_s * 2 ** (attempt - 1)
                delay = min(MAX_BACKOFF_S, _retry_after_s(exc) or base * random.uniform(0.8, 1.2))
                logger.warning(
                    "%s: %s, retry %d/%d in %.1fs",
                    what, type(exc).__name__, attempt, attempts - 1, delay,
                )  # fmt: skip
                self._sleep(delay)
        raise AssertionError("unreachable")

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        response_format: dict[str, Any] | None,
        on_error: OnError | None = None,
    ) -> CompletionResult:
        self._refuse_in_demo_mode(f"completion with {model}")
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "timeout": self.settings.llm_timeout_s,
            "num_retries": 0,  # retries are ours, so every attempt is logged
            "drop_params": True,  # e.g. reasoning models that reject temperature
        }
        if response_format is not None:
            kwargs["response_format"] = response_format

        start = time.perf_counter()
        resp = self._with_retries(
            f"completion {model}", lambda: litellm.completion(**kwargs), on_error
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        usage = getattr(resp, "usage", None)
        return CompletionResult(
            text=resp.choices[0].message.content or "",
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=latency_ms,
            raw=resp,
        )

    def embed(
        self,
        *,
        model: str,
        texts: list[str],
        dimensions: int,
        on_error: OnError | None = None,
    ) -> EmbeddingBatch:
        self._refuse_in_demo_mode(f"embedding with {model}")
        kwargs: dict[str, Any] = {
            "model": model,
            "input": texts,
            "dimensions": dimensions,
            "timeout": self.settings.llm_timeout_s,
            "num_retries": 0,
            "drop_params": True,
        }
        start = time.perf_counter()
        resp = self._with_retries(
            f"embedding {model}", lambda: litellm.embedding(**kwargs), on_error
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        items = sorted(resp.data, key=lambda d: _get(d, "index"))
        usage = getattr(resp, "usage", None)
        return EmbeddingBatch(
            vectors=[list(_get(d, "embedding")) for d in items],
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            latency_ms=latency_ms,
            raw=resp,
        )


def _get(item: Any, key: str) -> Any:
    return item[key] if isinstance(item, dict) else getattr(item, key)
