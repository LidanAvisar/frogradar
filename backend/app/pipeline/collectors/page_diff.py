"""page_diff collector: fetch a watched page, snapshot it when it changed, emit a change signal.

- Conditional GET (ETag / Last-Modified); 304 → nothing to do.
- Text = main content (trafilatura, or options.selector), normalized (app.pipeline.page_diff).
  options.sections configured but none matched → the source fails (redesign), no snapshot.
  Shorter than page_diff.min_text_chars → the source fails (JS shell / error page), no snapshot.
- Same hash as the latest snapshot → unchanged, nothing stored.
- Otherwise a new snapshot is stored (in the same transaction as the signal, by collect_source)
  and diffed against the reference snapshot (latest non-noise one). First fetch = baseline only.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.catalog import PageDiffSettings
from app.pipeline import snapshots
from app.pipeline.collectors.base import CollectResult, KnownUrls, SourceRef
from app.pipeline.collectors.http import PoliteClient
from app.pipeline.page_diff import ExtractionError, Normalizer, SnapshotInfo, extract_text


def page_text(
    html: str, source: SourceRef, cfg: PageDiffSettings, *, require_sections: bool = True
) -> str:
    """Normalized text of a page, or ExtractionError if too little survives.

    require_sections (default): a source with options.sections fails when none of them match
    (SectionsMissing) – the config describes today's page. The Wayback backfill turns it off for
    archived captures, which may predate the sectioned layout (Sonatype's 2025 page had no toggle).
    """
    selector = source.options.get("selector")
    sections = source.options.get("sections")
    raw = extract_text(html, selector, sections, require_sections=require_sections)
    text = Normalizer.from_settings(cfg).normalize(raw)
    if len(text) < cfg.min_text_chars:
        raise ExtractionError(
            f"only {len(text)} characters of text (min {cfg.min_text_chars}): "
            "page probably needs JavaScript or returned an error page"
        )
    return text


class PageDiffCollector:
    def __init__(
        self,
        client: PoliteClient,
        session_factory: Callable[[], Session],
        cfg: PageDiffSettings,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self.session_factory = session_factory
        self.cfg = cfg
        self.now = now

    def collect(
        self, source: SourceRef, *, since: datetime, backfill: bool, known: KnownUrls
    ) -> CollectResult:
        result = CollectResult()
        resp = self.client.get(
            source.url, etag=source.http_etag, last_modified=source.http_last_modified
        )
        result.etag = resp.headers.get("etag")
        result.last_modified = resp.headers.get("last-modified")
        if resp.status_code == 304:
            result.not_modified = True
            result.etag, result.last_modified = source.http_etag, source.http_last_modified
            return result

        text = page_text(resp.text, source, self.cfg)
        captured = self.now()
        new = snapshots.NewSnapshot(
            key="live", text=text, captured_at=captured, meta=snapshots.live_meta()
        )
        with self.session_factory() as s:
            last = snapshots.latest(s, source.id)
            ref = snapshots.reference(s, source.id)
            upgraded = last is None and snapshots.has_older_versions(s, source.id)
            if last is not None and last.content_hash == new.content_hash:
                result.notes.append("unchanged")
                return result
            ref_info = snapshots.info(ref) if ref is not None else None
            ref_text = ref.text if ref is not None else None

        result.snapshots.append(new)
        if ref_info is None or ref_text is None:
            new.meta["diff_kind"] = "baseline"
            result.notes.append(
                "baseline snapshot stored"
                + (" (text format changed: older snapshots are not compared)" if upgraded else "")
            )
            return result

        outcome = snapshots.compare(
            source, ref_info, ref_text, SnapshotInfo(None, captured, "live"), text, self.cfg
        )
        new.meta.update(outcome.after_meta)
        kind = outcome.result.kind
        ratio = outcome.result.full_stats.get("changed_line_ratio")
        note = f"{kind} vs snapshot {ref_info.id} (changed-line ratio {ratio})"
        if outcome.item is not None:
            outcome.item.meta["after_snapshot_key"] = new.key
            result.items.append(outcome.item)
            note += f", signal ({outcome.result.diff_mode})"
        result.notes.append(note)
        return result
