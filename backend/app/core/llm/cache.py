"""Content-hash cache of validated LLM outputs and embeddings (table llm_cache).

Bump CACHE_VERSION to invalidate every entry at once (e.g. after changing how outputs are stored).
"""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import LLMCache

CACHE_VERSION = 1


def _hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"cache_version": CACHE_VERSION, **payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def completion_key(
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
    thinking: str = "default",
) -> str:
    params: dict[str, Any] = {"max_tokens": max_tokens, "temperature": temperature}
    if thinking != "default":
        # Only when set, so keys of calls made before the setting existed stay valid.
        params["thinking"] = thinking
    return _hash(
        {
            "kind": "completion",
            "model": model,
            "params": params,
            "messages": messages,
            "schema": schema,
        }
    )


def embedding_key(*, model: str, dimensions: int, text: str) -> str:
    return _hash({"kind": "embedding", "model": model, "dimensions": dimensions, "text": text})


def get_many(session: Session, keys: list[str]) -> dict[str, LLMCache]:
    if not keys:
        return {}
    rows = session.scalars(select(LLMCache).where(LLMCache.cache_key.in_(keys)))
    return {row.cache_key: row for row in rows}


def mark_hits(session: Session, keys: list[str]) -> None:
    if keys:
        session.execute(
            update(LLMCache)
            .where(LLMCache.cache_key.in_(keys))
            .values(hit_count=LLMCache.hit_count + 1, last_hit_at=datetime.now(UTC))
        )


def put(
    session: Session,
    *,
    key: str,
    kind: str,
    model: str,
    operation: str,
    prompt_version: str | None,
    response: Any,
    tokens_in: int,
    tokens_out: int,
    cost_usd: Decimal | None,
) -> None:
    """Insert; if another worker cached the same key first, keep theirs."""
    session.execute(
        insert(LLMCache)
        .values(
            cache_key=key,
            kind=kind,
            model=model,
            operation=operation,
            prompt_version=prompt_version,
            response=response,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
        )
        .on_conflict_do_nothing(index_elements=["cache_key"])
    )
