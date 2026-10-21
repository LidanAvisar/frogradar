"""Collect raw signals from sources into the DB. No LLM calls, no semantic dedup.

    python -m app.pipeline.collect                       # all active sources
    python -m app.pipeline.collect --source "Snyk Blog"  # one source, by id or name
    python -m app.pipeline.collect --backfill --since 90d

One pipeline_runs row per run, with per-source counts. A failing source never stops the run.
"""

import argparse
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.enums import FetchMethod, RunStatus, RunTrigger
from app.db.models import PipelineRun, Source
from app.pipeline import snapshots, store
from app.pipeline.collectors import (
    Collector,
    NotImplementedCollector,
    PoliteClient,
    SourceRef,
    build_collectors,
)
from app.pipeline.store import Outcome

logger = logging.getLogger(__name__)

DEFAULT_SINCE_DAYS = 90
_MAX_ERROR_CHARS = 300


@dataclass
class SourceReport:
    source_id: int
    name: str
    fetch_method: str
    status: str = "ok"  # ok / not_modified / skipped / failed
    fetched: int = 0  # items the collector returned (after the --since filter)
    new: int = 0
    updated: int = 0
    duplicate: int = 0
    failed: int = 0  # items that could not be stored
    too_old: int = 0
    extract_failed: int = 0
    oldest_seen: str | None = None
    reached_since: bool = False
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def history(self) -> str:
        """How far back this source reached, in words (for the CLI/README)."""
        if self.status in ("skipped", "failed", "not_modified"):
            return "-"
        if self.reached_since:
            return "full window"
        if self.oldest_seen:
            return f"only back to {self.oldest_seen[:10]}"
        return "no items"


@dataclass
class RunReport:
    run_id: int
    status: RunStatus
    since: datetime
    backfill: bool
    sources: list[SourceReport]

    def totals(self) -> dict[str, int]:
        keys = ("fetched", "new", "updated", "duplicate", "failed", "too_old", "extract_failed")
        totals = {k: sum(getattr(s, k) for s in self.sources) for k in keys}
        for status in ("ok", "not_modified", "skipped", "failed"):
            totals[f"sources_{status}"] = sum(s.status == status for s in self.sources)
        return totals


def parse_since(value: str, now: datetime | None = None) -> datetime:
    """'90d' (days back) or 'YYYY-MM-DD' → UTC datetime."""
    now = now or datetime.now(UTC)
    if m := re.fullmatch(r"(\d+)d", value.strip()):
        return now - timedelta(days=int(m.group(1)))
    return datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=UTC)


def select_sources(session: Session, selector: str | None) -> list[Source]:
    """All active sources, or the one matching an id or (case-insensitive) name."""
    query = select(Source).order_by(Source.id)
    if selector is None:
        return list(session.scalars(query.where(Source.is_active)))
    conds = [func.lower(Source.name) == selector.lower()]
    if selector.isdigit():
        conds.append(Source.id == int(selector))
    found = list(session.scalars(query.where(or_(*conds))))
    if not found:
        raise SystemExit(f"no source with id or name {selector!r}")
    return found


def _ref(src: Source) -> SourceRef:
    return SourceRef(
        id=src.id,
        name=src.name,
        url=src.url,
        fetch_method=FetchMethod(src.fetch_method),
        options=dict(src.options or {}),
        entity_id=src.entity_id,
        http_etag=src.http_etag,
        http_last_modified=src.http_last_modified,
    )


def _short(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text if len(text) <= _MAX_ERROR_CHARS else text[:_MAX_ERROR_CHARS] + "…"


def collect_source(
    session_factory: Callable[[], Session],
    collector: Collector,
    ref: SourceRef,
    *,
    run_id: int,
    since: datetime,
    backfill: bool,
) -> SourceReport:
    report = SourceReport(ref.id, ref.name, ref.fetch_method.value)
    try:
        with session_factory() as s:
            known = store.known_urls(s, ref.id)
        result = collector.collect(ref, since=since, backfill=backfill, known=known)
    except NotImplementedCollector as e:
        report.status, report.error = "skipped", str(e)
        return report
    except Exception as e:  # noqa: BLE001 - one source must never stop the run
        logger.warning("source %s (%s) failed: %s", ref.id, ref.name, _short(e))
        report.status, report.error = "failed", _short(e)
        return report

    report.fetched = len(result.items)
    report.duplicate = result.skipped_known
    report.too_old = result.too_old
    report.extract_failed = result.extract_failed
    report.reached_since = result.reached_since
    report.oldest_seen = result.oldest_seen.isoformat() if result.oldest_seen else None
    report.notes = result.notes
    if result.not_modified:
        report.status = "not_modified"

    with session_factory() as s:
        # page_diff: the new snapshot goes in the same transaction as its change signal.
        snapshot_ids = {snap.key: snapshots.insert(s, ref.id, snap).id for snap in result.snapshots}
        for item in result.items:
            if (key := item.meta.pop("after_snapshot_key", None)) is not None:
                item.meta["after_snapshot_id"] = snapshot_ids[key]
            try:
                with s.begin_nested():
                    outcome = store.save_item(s, ref, run_id, item)
            except Exception as e:  # noqa: BLE001 - one bad item must not lose the others
                logger.warning("could not store %s: %s", item.url, _short(e))
                report.failed += 1
                continue
            match outcome:
                case Outcome.NEW:
                    report.new += 1
                case Outcome.UPDATED:
                    report.updated += 1
                case Outcome.DUPLICATE:
                    report.duplicate += 1
        src = s.get(Source, ref.id)
        if src is not None:
            src.last_fetched_at = datetime.now(UTC)
            src.http_etag = result.etag
            src.http_last_modified = result.last_modified
        s.commit()
    return report


def collect_all(
    *,
    session_factory: Callable[[], Session],
    client: PoliteClient,
    settings: Settings,
    run_id: int,
    source: str | None = None,
    since: datetime | None = None,
    backfill: bool = False,
) -> RunReport:
    """Collect every selected source into an existing pipeline run (each source fails alone)."""
    since = since or datetime.now(UTC) - timedelta(days=DEFAULT_SINCE_DAYS)
    collectors = build_collectors(client, settings, session_factory)
    with session_factory() as s:
        refs = [_ref(src) for src in select_sources(s, source)]

    reports = []
    for ref in refs:
        logger.info("collecting %s (%s)", ref.name, ref.fetch_method.value)
        reports.append(
            collect_source(
                session_factory,
                collectors[ref.fetch_method],
                ref,
                run_id=run_id,
                since=since,
                backfill=backfill,
            )
        )

    attempted = [r for r in reports if r.status != "skipped"]
    failed = [r for r in attempted if r.status == "failed"]
    if not failed:
        status = RunStatus.SUCCEEDED
    elif len(failed) < len(attempted):
        status = RunStatus.PARTIAL
    else:
        status = RunStatus.FAILED
    return RunReport(run_id, status, since, backfill, reports)


def failed_sources_summary(report: RunReport) -> str | None:
    failed = [r for r in report.sources if r.status == "failed"]
    return "; ".join(f"{r.name}: {r.error}" for r in failed)[:4000] or None


def run_collection(
    *,
    session_factory: Callable[[], Session],
    client: PoliteClient,
    settings: Settings,
    source: str | None = None,
    since: datetime | None = None,
    backfill: bool = False,
) -> RunReport:
    """`make collect`: a pipeline run of its own that only collects."""
    with session_factory() as s:
        run = PipelineRun(trigger=RunTrigger.BACKFILL if backfill else RunTrigger.MANUAL)
        s.add(run)
        s.commit()
        run_id = run.id

    report = collect_all(
        session_factory=session_factory,
        client=client,
        settings=settings,
        run_id=run_id,
        source=source,
        since=since,
        backfill=backfill,
    )
    with session_factory() as s:
        run = s.get(PipelineRun, run_id)
        run.status = report.status
        run.finished_at = datetime.now(UTC)
        run.counts = counts_json(report)
        run.error = failed_sources_summary(report)
        s.commit()
    return report


def counts_json(report: RunReport) -> dict[str, Any]:
    return {
        "kind": "collect",
        "since": report.since.isoformat(),
        "backfill": report.backfill,
        "totals": report.totals(),
        "sources": {str(r.source_id): {**asdict(r), "history": r.history} for r in report.sources},
    }


def print_report(report: RunReport, out=sys.stdout) -> None:
    cols = "{:>4} {:<34} {:<9} {:<12} {:>5} {:>5} {:>4} {:>5} {:>5} {:>4}  {}"
    print(
        cols.format(
            "id", "source", "method", "status", "items", "new", "upd", "dup", "old", "fail",
            "history / error",
        ),
        file=out,
    )  # fmt: skip
    for r in report.sources:
        detail = r.error if r.status in ("failed", "skipped") else r.history
        if r.extract_failed:
            detail += f" ({r.extract_failed} article extractions failed)"
        for note in r.notes:
            detail += f"; NOTE: {note}"
        print(
            cols.format(
                r.source_id, r.name[:34], r.fetch_method, r.status, r.fetched, r.new,
                r.updated, r.duplicate, r.too_old, r.failed, detail or "",
            ),
            file=out,
        )  # fmt: skip
    t = report.totals()
    print(
        f"\nrun {report.run_id}: {report.status.value} – since {report.since.date()}"
        f"{' (backfill)' if report.backfill else ''} – {t['new']} new, {t['updated']} updated, "
        f"{t['duplicate']} duplicate, {t['failed']} failed items; sources: {t['sources_ok']} ok, "
        f"{t['sources_not_modified']} not modified, {t['sources_skipped']} skipped, "
        f"{t['sources_failed']} failed",
        file=out,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", help="source id or exact name (default: all active)")
    parser.add_argument(
        "--since", default=f"{DEFAULT_SINCE_DAYS}d", help="'90d' or YYYY-MM-DD (default 90d)"
    )
    parser.add_argument(
        "--backfill", action="store_true", help="page through history; no conditional GET"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    from app.db.session import get_engine

    settings = get_settings()
    factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    with PoliteClient(settings) as client:
        report = run_collection(
            session_factory=factory,
            client=client,
            settings=settings,
            source=args.source,
            since=parse_since(args.since),
            backfill=args.backfill,
        )
        print_report(report)
        print(f"HTTP requests: {client.request_count}")
    return 0 if report.status is not RunStatus.FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
