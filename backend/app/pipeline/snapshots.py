"""page_snapshots storage and the snapshot-pair → signal step shared by the live page_diff
collector and the Wayback backfill.

Reference snapshot: a change is measured against the latest snapshot that was not just noise, so
small edits that are each below min_changed_chars still add up to a signal eventually.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.catalog import PageDiffSettings
from app.db.models import PageSnapshot
from app.pipeline.collectors.base import RawItem, SourceRef
from app.pipeline.normalize import canonical_url
from app.pipeline.page_diff import (
    NORMALIZER_VERSION,
    PairResult,
    SnapshotInfo,
    classify,
    render_diff,
    text_hash,
)

DEFAULT_LABEL = "Pricing page"


@dataclass
class NewSnapshot:
    """A snapshot to insert (the collector decides, collect_source writes it with the items)."""

    key: str  # referenced from RawItem.meta["after_snapshot_key"]
    text: str
    captured_at: datetime
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return text_hash(self.text)


def current_version() -> Any:
    """SQL: the snapshot was normalized with the current NORMALIZER_VERSION (D40 addendum).
    Texts of different versions are never compared: the format change would look like edits."""
    return PageSnapshot.meta["normalizer"].astext == str(NORMALIZER_VERSION)


def older_version() -> Any:
    """SQL: any other version, including v1 rows that have no "normalizer" key (NULL)."""
    return PageSnapshot.meta["normalizer"].astext.is_distinct_from(str(NORMALIZER_VERSION))


def live_meta() -> dict[str, Any]:
    return {"origin": "live", "normalizer": NORMALIZER_VERSION}


def wayback_meta(timestamp: str) -> dict[str, Any]:
    return {"origin": "wayback", "wayback_timestamp": timestamp, "normalizer": NORMALIZER_VERSION}


def has_older_versions(session: Session, source_id: int) -> bool:
    return (
        session.scalars(
            select(PageSnapshot.id)
            .where(PageSnapshot.source_id == source_id, older_version())
            .limit(1)
        ).first()
        is not None
    )


def is_current(row: PageSnapshot) -> bool:
    return row.meta.get("normalizer") == NORMALIZER_VERSION


def upgrade(row: PageSnapshot, text: str) -> None:
    """Re-normalized text of the same capture (same page, same moment) with the current version."""
    old = row.meta.get("normalizer", 1)
    row.text = text
    row.content_hash = text_hash(text)
    row.meta = {**row.meta, "normalizer": NORMALIZER_VERSION, "upgraded_from_normalizer": old}


def latest(session: Session, source_id: int) -> PageSnapshot | None:
    """Latest snapshot of the current normalizer version."""
    return session.scalars(
        select(PageSnapshot)
        .where(PageSnapshot.source_id == source_id, current_version())
        .order_by(PageSnapshot.captured_at.desc(), PageSnapshot.id.desc())
        .limit(1)
    ).first()


def reference(session: Session, source_id: int) -> PageSnapshot | None:
    """Latest snapshot not marked as noise: the baseline a new version is diffed against."""
    return session.scalars(
        select(PageSnapshot)
        .where(
            PageSnapshot.source_id == source_id,
            current_version(),
            PageSnapshot.meta["diff_kind"].astext.is_distinct_from("noise"),
        )
        .order_by(PageSnapshot.captured_at.desc(), PageSnapshot.id.desc())
        .limit(1)
    ).first()


def by_wayback_timestamp(session: Session, source_id: int, timestamp: str) -> PageSnapshot | None:
    return session.scalars(
        select(PageSnapshot).where(
            PageSnapshot.source_id == source_id,
            PageSnapshot.meta["wayback_timestamp"].astext == timestamp,
        )
    ).first()


def insert(session: Session, source_id: int, snap: NewSnapshot) -> PageSnapshot:
    row = PageSnapshot(
        source_id=source_id,
        content_hash=snap.content_hash,
        text=snap.text,
        captured_at=snap.captured_at,
        meta=snap.meta,
    )
    session.add(row)
    session.flush()
    return row


def info(row: PageSnapshot) -> SnapshotInfo:
    return SnapshotInfo(row.id, row.captured_at, str(row.meta.get("origin", "live")))


@dataclass
class PairOutcome:
    result: PairResult
    item: RawItem | None
    # Fields to merge into the "after" snapshot's meta (diff_kind, page_restructured, ...).
    after_meta: dict[str, Any]

    def summary(self, before: SnapshotInfo, after: SnapshotInfo) -> dict[str, Any]:
        out: dict[str, Any] = {
            "before": before.captured_at.date().isoformat(),
            "after": after.captured_at.date().isoformat(),
            "kind": self.result.kind,
            "signal": self.item is not None,
            **{k: v for k, v in self.result.full_stats.items() if k != "price_diff"},
        }
        if self.result.diff_mode:
            out["diff_mode"] = self.result.diff_mode
        return out


def _stamp(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%d%H%M%S")


def compare(
    source: SourceRef,
    before: SnapshotInfo,
    before_text: str,
    after: SnapshotInfo,
    after_text: str,
    cfg: PageDiffSettings,
) -> PairOutcome:
    """Classify one pair and build the signal item if the change warrants one."""
    result = classify(before_text, after_text, cfg)
    after_meta: dict[str, Any] = {"diff_kind": result.kind, "diffed_against": before.id}
    if result.kind == "restructured":
        after_meta["page_restructured"] = True
        after_meta["changed_line_ratio"] = result.full_stats.get("changed_line_ratio")
    if not result.creates_signal:
        return PairOutcome(result, None, after_meta)

    text, truncated = render_diff(
        source.name, source.url, before, after, result, cfg.max_diff_chars
    )
    label = str(source.options.get("label") or DEFAULT_LABEL)
    base = canonical_url(source.url)
    meta: dict[str, Any] = {
        "kind": "page_diff",
        "text_source": "diff",
        "before_snapshot_id": before.id,
        "after_snapshot_id": after.id,
        "before_captured_at": before.captured_at.isoformat(),
        "after_captured_at": after.captured_at.isoformat(),
        "before_origin": before.origin,
        "after_origin": after.origin,
        "diff_mode": result.diff_mode,
        "diff_stats": result.full_stats,
        "page_restructured": result.kind == "restructured",
        "truncated": truncated,
    }
    item = RawItem(
        url=source.url,
        canonical_url=f"{base}#diff-{_stamp(before.captured_at)}-{_stamp(after.captured_at)}",
        title=f"{label} changed: {source.name}",
        text=text,
        published_at=after.captured_at,
        meta=meta,
    )
    return PairOutcome(result, item, after_meta)
