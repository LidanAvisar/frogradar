"""LLM gateway errors. Callers catch these and decide what to do (skip, stop, report)."""

import re

# (pattern, replacement), applied in order.
_SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "***"),  # OpenAI / Anthropic style keys
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "***"),  # Google keys
    (re.compile(r"(?i)\b(bearer|basic)\s+[^\s\"',}]+"), r"\1 ***"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|x-api-key|authorization)([\"'=:\s]+)(?!bearer\b|basic\b)[^\s\"',}:=]+"
        ),
        r"\1\2***",
    ),
]


def scrub(text: str, limit: int = 500) -> str:
    """Remove anything that looks like a credential, then truncate. Use before logging/storing."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text if len(text) <= limit else text[:limit] + "…"


class LLMError(RuntimeError):
    """Base class for gateway errors."""


class BudgetExceeded(LLMError):
    """Today's LLM spend reached LLM_DAILY_BUDGET_USD."""


class MissionBudgetExceeded(BudgetExceeded):
    """A research mission's LLM spend reached missions.budget_usd."""


class StructuredOutputError(LLMError):
    """The model's output failed schema validation on every attempt."""


class ProviderError(LLMError):
    """The provider call failed: a non-retryable error (auth, bad request) or retries ran out."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
