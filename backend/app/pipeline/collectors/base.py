"""Common collector interface: a source in, normalized items out."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.db.enums import FetchMethod


@dataclass(frozen=True)
class SourceRef:
    """What a collector needs to know about a source (a detached snapshot of the DB row)."""

    id: int
    name: str
    url: str
    fetch_method: FetchMethod
    options: dict[str, Any]
    entity_id: int | None = None
    http_etag: str | None = None
    http_last_modified: str | None = None


@dataclass
class RawItem:
    """One normalized item, ready to become a signal."""

    url: str
    canonical_url: str
    title: str
    text: str
    published_at: datetime | None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectResult:
    items: list[RawItem] = field(default_factory=list)
    # Items whose URL is already stored and weren't re-fetched (counted as duplicates).
    skipped_known: int = 0
    too_old: int = 0  # older than --since, dropped
    extract_failed: int = 0  # article fetch/extraction failed; the feed text was kept
    oldest_seen: datetime | None = None  # oldest item the source offered (incl. too-old ones)
    reached_since: bool = False  # True if the source had items at/older than --since
    not_modified: bool = False  # conditional GET returned 304
    # New conditional-GET validators to store on the source.
    etag: str | None = None
    last_modified: str | None = None
    notes: list[str] = field(default_factory=list)
    # page_diff: snapshots to insert with the items (app.pipeline.snapshots.NewSnapshot).
    snapshots: list[Any] = field(default_factory=list)

    def see(self, published_at: datetime | None, since: datetime) -> bool:
        """Track history depth; return False if the item is older than `since`."""
        if published_at is None:
            return True
        if self.oldest_seen is None or published_at < self.oldest_seen:
            self.oldest_seen = published_at
        if published_at < since:
            self.reached_since = True
            self.too_old += 1
            return False
        return True


# canonical_url -> text_source of the stored signal ("feed", "article", "api"), for this source.
KnownUrls = dict[str, str]


class Collector(Protocol):
    def collect(
        self, source: SourceRef, *, since: datetime, backfill: bool, known: KnownUrls
    ) -> CollectResult: ...


class NotImplementedCollector(Exception):
    """Raised by stub collectors; the run marks the source as skipped, not failed."""


CollectorFactory = Callable[..., Collector]
