"""Store collected items as signals.

Identity rules:
- Within a source, a signal is identified by canonical_url (unique (source_id, canonical_url)).
  Same URL with the same content → duplicate. Same URL with different content (re-extraction,
  edited release notes) → update title/raw_text/content_hash in place, count a revision in meta,
  and clear enriched_at so the enrich step re-processes it.
- Across sources, content_hash catches exact copies (syndication): ON CONFLICT DO NOTHING.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.enums import Verification
from app.db.models import Signal, signal_entities
from app.pipeline.collectors.base import KnownUrls, RawItem, SourceRef
from app.pipeline.hashing import content_hash


class Outcome(StrEnum):
    NEW = "new"
    UPDATED = "updated"
    DUPLICATE = "duplicate"


def item_hash(item: RawItem) -> str:
    return content_hash(f"{item.title}\n\n{item.text}")


def known_urls(session: Session, source_id: int) -> KnownUrls:
    rows = session.execute(
        select(Signal.canonical_url, Signal.meta["text_source"].astext).where(
            Signal.source_id == source_id
        )
    )
    return {url: text_source or "" for url, text_source in rows}


@dataclass
class _Existing:
    id: int
    content_hash: str
    meta: dict[str, Any]


def save_item(session: Session, source: SourceRef, run_id: int | None, item: RawItem) -> Outcome:
    """Insert, update or skip one item. Caller handles transactions (one savepoint per item)."""
    h = item_hash(item)
    row = session.execute(
        select(Signal.id, Signal.content_hash, Signal.meta).where(
            Signal.source_id == source.id, Signal.canonical_url == item.canonical_url
        )
    ).first()

    if row is not None:
        existing = _Existing(*row)
        if existing.content_hash == h:
            return Outcome.DUPLICATE
        # The new text is an exact copy of another signal: keep ours as is.
        if session.scalar(select(exists().where(Signal.content_hash == h))):
            return Outcome.DUPLICATE
        meta = {
            **existing.meta,
            **item.meta,
            "revisions": int(existing.meta.get("revisions", 0)) + 1,
            "last_changed_at": datetime.now(UTC).isoformat(),
            "last_changed_run_id": run_id,
        }
        session.execute(
            update(Signal)
            .where(Signal.id == existing.id)
            .values(
                url=item.url,
                title=item.title,
                raw_text=item.text,
                content_hash=h,
                meta=meta,
                enriched_at=None,
                verification=Verification.PENDING,
            )
        )
        return Outcome.UPDATED

    signal_id = session.scalar(
        insert(Signal)
        .values(
            source_id=source.id,
            run_id=run_id,
            url=item.url,
            canonical_url=item.canonical_url,
            content_hash=h,
            title=item.title,
            raw_text=item.text,
            published_at=item.published_at,
            meta=item.meta,
        )
        .on_conflict_do_nothing()  # content_hash or (source_id, canonical_url)
        .returning(Signal.id)
    )
    if signal_id is None:
        return Outcome.DUPLICATE
    if source.entity_id is not None:
        session.execute(
            insert(signal_entities)
            .values(signal_id=signal_id, entity_id=source.entity_id, is_primary=True)
            .on_conflict_do_nothing()
        )
    return Outcome.NEW
