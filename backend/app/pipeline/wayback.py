"""Historical baseline for page_diff sources from the Wayback Machine (D41).

    python -m app.pipeline.wayback                    # every active page_diff source
    python -m app.pipeline.wayback --source "GitHub Pricing"

For each source:
1. Fetch today's page (reuse today's live snapshot if the text is unchanged).
2. One CDX query lists the archived 200/HTML captures (one per day) of the last
   max(target_days_ago) + window_days days; for each target (~12, 6, 3 months ago) the closest
   capture within window_days is picked. No capture in the window → reported as missing.
3. Each pick is fetched raw (`id_` URL: the original HTML without the Wayback toolbar) and
   checked: enough text, at least min_length_ratio of today's length, and price lines present
   if today's page has them. A capture that fails is reported (rendered differently, probably
   JS-built) and NOT stored, so no misleading diff is produced across it.
4. Snapshots are stored (meta: origin=wayback, wayback_timestamp) and the source's snapshots are
   diffed in captured_at order. Changes become signals dated to the later snapshot.

Snapshots stored in an older text format (normalizer version) are re-fetched and upgraded in place
(Wayback captures; live snapshots only if taken today). --only-after limits which change signals
are written, so a re-render can be confined to the pairs that need it.

Idempotent: a capture already stored (same wayback timestamp) is reused, and change signals are
keyed by (source, url#diff-<before>-<after>). web.archive.org gets a 5 s gap between requests
(page_diff.wayback.host_delay_s) and its robots.txt is honoured by PoliteClient.
"""

import argparse
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.catalog import PageDiffSettings, load_pipeline
from app.core.config import get_settings
from app.db.enums import FetchMethod, RunStatus, RunTrigger
from app.db.models import PageSnapshot, PipelineRun
from app.pipeline import snapshots, store
from app.pipeline.collect import _ref, _short, select_sources
from app.pipeline.collectors import FetchError, PoliteClient, SourceRef
from app.pipeline.collectors.page_diff import page_text
from app.pipeline.lock import PipelineLocked, pipeline_lock
from app.pipeline.page_diff import ExtractionError, price_lines
from app.pipeline.store import Outcome

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Capture:
    timestamp: str  # Wayback 14-digit timestamp
    at: datetime


def parse_timestamp(ts: str) -> datetime:
    ts = ts[:14]
    full = ts + "19700101000000"[len(ts) :]  # pad a partial timestamp to the period's start
    return datetime.strptime(full, "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def cdx_url(cfg: PageDiffSettings, page_url: str, since: datetime, until: datetime) -> str:
    params = [
        ("url", page_url),
        ("output", "json"),
        ("fl", "timestamp,statuscode,mimetype"),
        ("filter", "statuscode:200"),
        ("filter", "mimetype:text/html"),
        ("collapse", "timestamp:8"),  # one capture per day
        ("from", since.strftime("%Y%m%d")),
        ("to", until.strftime("%Y%m%d")),
    ]
    return f"{cfg.wayback.cdx_url}?{urlencode(params)}"


def parse_cdx(rows: Any) -> list[Capture]:
    """CDX JSON: a header row, then one list per capture. Sorted by time."""
    if not rows:
        return []
    header, *data = rows
    i = header.index("timestamp")
    return sorted((Capture(r[i], parse_timestamp(r[i])) for r in data), key=lambda c: c.at)


def pick_closest(
    captures: list[Capture], targets: list[datetime], window: timedelta
) -> tuple[dict[datetime, Capture], list[datetime]]:
    """Closest capture per target (within the window). Returns (picks, targets without one)."""
    picks: dict[datetime, Capture] = {}
    missing: list[datetime] = []
    for target in sorted(targets):
        best = min(captures, key=lambda c: abs(c.at - target), default=None)
        if best is None or abs(best.at - target) > window:
            missing.append(target)
        else:
            picks[target] = best
    return picks, missing


@dataclass
class SourceBackfill:
    source_id: int
    name: str
    status: str = "ok"  # ok / failed
    error: str | None = None
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    pairs: list[dict[str, Any]] = field(default_factory=list)
    signals_new: int = 0
    signals_updated: int = 0  # same change, re-rendered text (enrichment is redone)
    signals_existing: int = 0

    def as_json(self) -> dict[str, Any]:
        return dict(self.__dict__)


def check_archived(text: str, today: str, cfg: PageDiffSettings) -> str | None:
    """Why an archived capture can't be compared with today's page, or None if it can."""
    ratio = len(text) / len(today) if today else 1.0
    if ratio < cfg.wayback.min_length_ratio:
        return (
            f"rendered differently: {len(text)} chars vs {len(today)} today "
            f"(ratio {ratio:.2f} < {cfg.wayback.min_length_ratio})"
        )
    patterns = [re.compile(p, re.IGNORECASE) for p in cfg.price_patterns]
    today_prices = price_lines(today.splitlines(), patterns)
    if today_prices and not price_lines(text.splitlines(), patterns):
        return "rendered differently: no price lines in the archived text (today has some)"
    return None


def backfill_source(
    factory: Callable[[], Session],
    client: PoliteClient,
    ref: SourceRef,
    cfg: PageDiffSettings,
    *,
    run_id: int,
    now: datetime,
    only_after: date | None = None,
) -> SourceBackfill:
    """only_after: write change signals only for pairs whose later snapshot is on/after this date
    (others are diffed and reported but their signals are left as they are)."""
    report = SourceBackfill(ref.id, ref.name)
    wb = cfg.wayback

    # 1. Today's page.
    today_text = page_text(client.get(ref.url).text, ref, cfg)

    # 2. Captures and picks.
    window = timedelta(days=wb.window_days)
    targets = [now - timedelta(days=d) for d in wb.target_days_ago]
    since = min(targets) - window
    cdx = client.get(cdx_url(cfg, ref.url, since, now))
    captures = parse_cdx(cdx.json() if cdx.content.strip() else [])
    picks, missing = pick_closest(captures, targets, window)
    for target in missing:
        report.missing.append(
            {"target": target.date().isoformat(), "reason": f"no capture within ±{wb.window_days}d"}
        )

    with factory() as s:
        # 3. Archived snapshots (fetched once; reused on re-runs).
        seen: set[str] = set()
        for target, cap in sorted(picks.items()):
            if cap.timestamp in seen:
                continue
            seen.add(cap.timestamp)
            entry = {"target": target.date().isoformat(), "captured": cap.at.date().isoformat()}
            row = snapshots.by_wayback_timestamp(s, ref.id, cap.timestamp)
            if row is not None and snapshots.is_current(row):
                reused_entry = {**entry, "id": row.id, "origin": "wayback", "status": "reused"}
                report.snapshots.append(reused_entry)
                continue
            # New capture, or a stored one in an older text format: (re-)fetch the archived HTML.
            url = wb.snapshot_url.format(timestamp=cap.timestamp, url=ref.url)
            try:
                text = page_text(client.get(url).text, ref, cfg)
            except (FetchError, ExtractionError) as e:
                report.rejected.append({**entry, "reason": _short(e)})
                continue
            if reason := check_archived(text, today_text, cfg):
                report.rejected.append({**entry, "reason": reason})
                continue
            if row is not None:
                snapshots.upgrade(row, text)
                status = "upgraded"
            else:
                snap = snapshots.NewSnapshot(
                    key=cap.timestamp, text=text, captured_at=cap.at,
                    meta=snapshots.wayback_meta(cap.timestamp),
                )  # fmt: skip
                row = snapshots.insert(s, ref.id, snap)
                status = "new"
            report.snapshots.append(
                {**entry, "id": row.id, "origin": "wayback", "chars": len(text), "status": status}
            )

        # Live snapshots from today in an older text format: same page, same day → upgrade.
        upgraded_live: set[int] = set()
        for old in s.scalars(
            select(PageSnapshot).where(
                PageSnapshot.source_id == ref.id,
                snapshots.older_version(),
                PageSnapshot.meta["origin"].astext == "live",
            )
        ):
            if old.captured_at.astimezone(UTC).date() == now.date():
                snapshots.upgrade(old, today_text)
                upgraded_live.add(old.id)
        s.flush()
        # Today's snapshot: reuse the latest one if the text is the same.
        last = snapshots.latest(s, ref.id)
        live = snapshots.NewSnapshot("live", today_text, now, snapshots.live_meta())
        if last is not None and last.content_hash == live.content_hash:
            today_row = last
            status = "upgraded" if last.id in upgraded_live else "reused"
        else:
            today_row = snapshots.insert(s, ref.id, live)
            status = "new"
        report.snapshots.append(
            {
                "captured": today_row.captured_at.date().isoformat(),
                "id": today_row.id,
                "origin": today_row.meta.get("origin", "live"),
                "chars": len(today_row.text),
                "status": status,
            }
        )

        # 4. Diff the chain in date order (only snapshots at/after the oldest one we just used).
        # Only snapshots in the current text format are compared with each other.
        chain = list(
            s.scalars(
                select(PageSnapshot)
                .where(PageSnapshot.source_id == ref.id, snapshots.current_version())
                .order_by(PageSnapshot.captured_at, PageSnapshot.id)
            )
        )
        base: PageSnapshot | None = None
        for snap_row in chain:
            if base is None:
                base = snap_row
                if not snap_row.meta.get("diff_kind"):
                    snap_row.meta = {**snap_row.meta, "diff_kind": "baseline"}
                continue
            if snap_row.content_hash == base.content_hash:
                continue
            before, after = snapshots.info(base), snapshots.info(snap_row)
            outcome = snapshots.compare(ref, before, base.text, after, snap_row.text, cfg)
            snap_row.meta = {**snap_row.meta, **outcome.after_meta}
            report.pairs.append(outcome.summary(before, after))
            if outcome.item is not None and only_after and after.captured_at.date() < only_after:
                report.pairs[-1]["signal"] = "left as is (--only-after)"
            elif outcome.item is not None:
                with s.begin_nested():
                    result = store.save_item(s, ref, run_id, outcome.item)
                if result is Outcome.NEW:
                    report.signals_new += 1
                elif result is Outcome.UPDATED:
                    report.signals_updated += 1
                else:
                    report.signals_existing += 1
            if outcome.result.kind != "noise":
                base = snap_row
        s.commit()
    return report


def run_backfill(
    factory: sessionmaker[Session],
    client: PoliteClient,
    *,
    source: str | None = None,
    cfg: PageDiffSettings | None = None,
    now: datetime | None = None,
    only_after: date | None = None,
) -> tuple[int, RunStatus, list[SourceBackfill]]:
    cfg = cfg or load_pipeline().page_diff
    now = now or datetime.now(UTC)
    with factory.begin() as s:
        run = PipelineRun(trigger=RunTrigger.BACKFILL, counts={"kind": "wayback"})
        s.add(run)
        s.flush()
        run_id = run.id
        refs = [
            _ref(src)
            for src in select_sources(s, source)
            if src.fetch_method == FetchMethod.PAGE_DIFF
        ]

    reports = []
    for ref in refs:
        logger.info("wayback backfill: %s", ref.name)
        try:
            reports.append(
                backfill_source(
                    factory, client, ref, cfg, run_id=run_id, now=now, only_after=only_after
                )
            )
        except Exception as e:  # noqa: BLE001 - one source must not stop the others
            logger.warning("wayback backfill of %s failed: %s", ref.name, _short(e))
            reports.append(SourceBackfill(ref.id, ref.name, status="failed", error=_short(e)))

    failed = sum(r.status == "failed" for r in reports)
    status = (
        RunStatus.SUCCEEDED
        if not failed
        else RunStatus.PARTIAL
        if failed < len(reports)
        else RunStatus.FAILED
    )
    with factory.begin() as s:
        run = s.get_one(PipelineRun, run_id)
        run.status = status
        run.finished_at = datetime.now(UTC)
        sources = {str(r.source_id): r.as_json() for r in reports}
        run.counts = {"kind": "wayback", "sources": sources}
        run.error = "; ".join(f"{r.name}: {r.error}" for r in reports if r.error)[:4000] or None
    return run_id, status, reports


def print_report(reports: list[SourceBackfill], out=sys.stdout) -> None:
    for r in reports:
        print(f"\n== {r.name} (source {r.source_id}): {r.status}", file=out)
        if r.error:
            print(f"   error: {r.error}", file=out)
        for snap in r.snapshots:
            target = f" (target {snap['target']})" if "target" in snap else ""
            print(
                f"   snapshot #{snap['id']} {snap['captured']} {snap['origin']}{target}, "
                f"{snap['status']}",
                file=out,
            )
        for m in r.missing:
            print(f"   MISSING target {m['target']}: {m['reason']}", file=out)
        for rej in r.rejected:
            when = f"{rej['captured']} (target {rej['target']})"
            print(f"   REJECTED {when}: {rej['reason']}", file=out)
        for p in r.pairs:
            mode = f" [{p['diff_mode']}]" if p.get("diff_mode") else ""
            print(
                f"   {p['before']} → {p['after']}: {p['kind']}{mode}, "
                f"changed-line ratio {p.get('changed_line_ratio')}, "
                f"{p.get('removed', 0)} removed / {p.get('added', 0)} added"
                + (f", signal {p['signal']}" if isinstance(p["signal"], str) else "")
                + (", signal" if p["signal"] is True else ""),
                file=out,
            )
        print(
            f"   signals: {r.signals_new} new, {r.signals_updated} updated, "
            f"{r.signals_existing} already stored",
            file=out,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", help="source id or exact name (default: active page_diff)")
    parser.add_argument(
        "--only-after",
        type=date.fromisoformat,
        help="YYYY-MM-DD: write change signals only for pairs whose later snapshot is on/after it",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    from app.db.session import get_engine

    settings = get_settings()
    cfg = load_pipeline().page_diff
    factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    host = urlsplit(cfg.wayback.cdx_url).netloc
    try:
        with (
            pipeline_lock(get_engine()),  # don't race the daily run's page_diff collector
            PoliteClient(settings, host_delays={host: cfg.wayback.host_delay_s}) as client,
        ):
            run_id, status, reports = run_backfill(
                factory, client, source=args.source, cfg=cfg, only_after=args.only_after
            )
            print_report(reports)
            print(f"\nrun {run_id}: {status.value} – HTTP requests: {client.request_count}")
    except PipelineLocked as e:
        print(f"not started: {e}", file=sys.stderr)
        return 2
    return 0 if status is not RunStatus.FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
