"""Page diff watcher (D40) and Wayback baseline (D41) with saved HTML fixtures (no network)."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.catalog import load_pipeline
from app.db.enums import FetchMethod, RunStatus, SourceKind
from app.db.models import PageSnapshot, Signal, Source
from app.pipeline import page_diff as pd
from app.pipeline.collect import run_collection
from app.pipeline.page_diff import FORMAT_NOTE
from app.pipeline.wayback import (
    Capture,
    backfill_source,
    cdx_url,
    parse_cdx,
    parse_timestamp,
    pick_closest,
    run_backfill,
)
from tests.pipeline.conftest import fixture, make_settings
from tests.pipeline.factories import entity

CFG = load_pipeline().page_diff
URL = "https://acme.test/pricing"


def text(name: str, selector: str | None = None) -> str:
    html = fixture(f"{name}.html").decode()
    return pd.Normalizer.from_settings(CFG).normalize(pd.extract_text(html, selector))


def at(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=UTC)


# ---------- normalization ----------


def test_normalization_strips_noise_and_keeps_prices():
    raw = "\n".join(
        [
            "Updated October 3, 2026",
            "We use cookies to improve your experience.",
            "Team",
            "$",
            "4",
            "per user/month",
            "Everything   in Free,\tplus private repositories for teams",
            "Everything in Free, plus private repositories for teams",
            "© 2024 - 2026 Acme Inc. All rights reserved by Acme Inc, really.",
            "Posted 3 days ago in the Acme community forum by the team",
        ]
    )
    lines = pd.Normalizer.from_settings(CFG).lines(raw)
    assert lines == [
        "Updated Team $ 4 per user/month",  # date gone; short lines joined into one
        "Everything in Free, plus private repositories for teams",  # repeat dropped
        "Acme Inc. All rights reserved by Acme Inc, really.",
        "Posted in the Acme community forum by the team",
    ]


def test_selector_and_trafilatura_agree_and_cookie_nav_footer_are_gone():
    full, main = text("pricing_v1"), text("pricing_v1", selector="main")
    assert full == main
    assert "cookie" not in full.lower() and "Sign in" not in full and "©" not in full
    assert "Team $4 per user/month" in full.splitlines()


def test_selector_matching_nothing_is_an_extraction_error():
    with pytest.raises(pd.ExtractionError):
        pd.extract_text(fixture("pricing_v1.html").decode(), "#does-not-exist")


# ---------- classification ----------


def test_no_change_after_normalization():
    # Different date, copyright years, whitespace and cookie wording: same normalized text.
    assert text("pricing_v1_noise") == text("pricing_v1")
    assert pd.classify(text("pricing_v1"), text("pricing_v1_noise"), CFG).kind == "no_change"


def test_reordering_is_ignored():
    r = pd.classify(text("pricing_v1"), text("pricing_v1_reorder"), CFG)
    assert r.kind == "noise" and not r.creates_signal
    assert r.full_stats["moved"] == 3 and r.full_stats["removed"] == 0


def test_small_wording_change_is_noise():
    before = text("pricing_v1")
    after = before.replace("Can I change plans later?", "Can I switch plans later?")
    r = pd.classify(before, after, CFG)
    assert r.kind == "noise" and r.full_stats["changed_chars"] < CFG.min_changed_chars


def test_one_digit_price_change_is_never_noise():
    before = text("pricing_v1")
    r = pd.classify(before, before.replace("Team $4", "Team $5"), CFG)
    assert r.kind == "changed" and r.creates_signal
    assert r.full_stats["changed_chars"] < CFG.min_changed_chars  # small, but a price


def test_real_change_price_and_feature_moved_between_tiers():
    r = pd.classify(text("pricing_v1"), text("pricing_v2"), CFG)
    assert r.kind == "changed" and r.diff_mode == "full"
    assert r.full_stats["tier_moves"] == 1  # malicious package blocking: Enterprise → Team
    raw, truncated = pd.render_diff(
        "Acme Pricing", URL, pd.SnapshotInfo(1, at("2026-04-01"), "wayback"),
        pd.SnapshotInfo(2, at("2026-10-01"), "live"), r, 12000,
    )  # fmt: skip
    assert not truncated
    assert "Before: 2026-04-01 (wayback)\nAfter: 2026-10-01 (live)" in raw
    assert "[before]\nTeam $4 per user/month\n[after]\nTeam $5 per user/month" in raw
    assert FORMAT_NOTE in raw
    assert (
        '- "Malicious package blocking for npm, PyPI and Maven": from the section headed '
        '"Enterprise $21 per user/month" (old page) to the section headed '
        '"Team $5 per user/month" (new page)'
    ) in raw
    after = pd.after_text(raw)
    assert "Team $5 per user/month" in after and "Team $4" not in after
    assert "Malicious package blocking for npm, PyPI and Maven" in after
    assert "Acme Pricing" not in after and "from the section" not in after  # not quotable


def test_a_multi_price_context_row_is_not_rendered_as_a_change():
    # Regression (run 15): an unchanged comparison row "Unlimited $4 ... $21 ..." near a
    # change was read as "$4 -> $21". It may appear only as [context], with the format note.
    before = text("pricing_v1").splitlines()
    before.insert(5, "Unlimited $4 per user/month $21 per user/month")
    after = list(before)
    after[6] = "2 GB of artifact storage, now with 1 GB of package cache, for every workspace"
    r = pd.classify("\n".join(before), "\n".join(after), CFG)
    snap = pd.SnapshotInfo(None, at("2026-10-01"), "live")
    raw, _ = pd.render_diff("A", URL, snap, snap, r, 12000)
    assert "[context]\nUnlimited $4 per user/month $21 per user/month\n[before]" in raw
    assert "[section" not in raw and FORMAT_NOTE in raw
    assert "Unlimited $4" not in pd.after_text(raw)


def test_redesign_with_a_price_change_uses_the_price_lines_only():
    r = pd.classify(text("pricing_v1"), text("pricing_redesign"), CFG)
    assert r.kind == "restructured" and r.diff_mode == "price_lines"
    assert r.full_stats["changed_line_ratio"] > CFG.restructure_ratio
    assert r.diff is not None and len(r.diff.hunks) == 1
    assert r.diff.hunks[0].before == ["Enterprise $21 per user/month"]
    assert r.diff.hunks[0].after == ["Enterprise $19 per user/month"]


def test_redesign_with_the_same_prices_creates_no_signal():
    r = pd.classify(text("pricing_v1"), text("pricing_redesign_same_prices"), CFG)
    assert r.kind == "restructured" and not r.creates_signal
    assert r.full_stats["changed_line_ratio"] > CFG.restructure_ratio


def test_long_diffs_are_truncated_at_a_change_boundary():
    before = text("pricing_v1")
    after = before.replace("Team $4", "Team $5")
    r = pd.classify(before, after, CFG)
    snap = pd.SnapshotInfo(None, at("2026-10-01"), "live")
    out, truncated = pd.render_diff("A", URL, snap, snap, r, 1000)
    assert not truncated
    r.diff.hunks *= 30  # type: ignore[union-attr]
    out, truncated = pd.render_diff("A", URL, snap, snap, r, 1000)
    assert truncated and len(out) <= 1000 + 40 and out.endswith("further changes truncated]")


# ---------- live collector ----------


@pytest.fixture
def pricing(collect_db):
    with collect_db.begin() as s:
        acme = entity(s, "acme", "Acme")
        src = Source(
            name="Acme Pricing", url=URL, kind=SourceKind.OFFICIAL,
            fetch_method=FetchMethod.PAGE_DIFF, trust_score=0.9, entity_id=acme.id,
        )  # fmt: skip
        s.add(src)
        s.flush()
        return src.id


def _collect(collect_db, web, make_client, page: str):
    web.add(URL, fixture(f"{page}.html"))
    report = run_collection(
        session_factory=collect_db, client=make_client(), settings=make_settings()
    )
    return report.sources[0]


def _rows(db, model, source_id):
    with db() as s:
        order = model.captured_at if model is PageSnapshot else model.id
        return list(s.scalars(select(model).where(model.source_id == source_id).order_by(order)))


def test_collector_baseline_unchanged_change_and_redesign(pricing, collect_db, web, make_client):
    first = _collect(collect_db, web, make_client, "pricing_v1")
    assert first.status == "ok" and first.new == 0 and first.notes == ["baseline snapshot stored"]
    assert len(_rows(collect_db, PageSnapshot, pricing)) == 1

    # Same text after normalization: nothing stored.
    again = _collect(collect_db, web, make_client, "pricing_v1_noise")
    assert again.notes == ["unchanged"] and len(_rows(collect_db, PageSnapshot, pricing)) == 1

    # Real change → snapshot + signal in one transaction.
    change = _collect(collect_db, web, make_client, "pricing_v2")
    assert change.new == 1
    snaps = _rows(collect_db, PageSnapshot, pricing)
    (sig,) = _rows(collect_db, Signal, pricing)
    assert len(snaps) == 2 and snaps[1].meta["diff_kind"] == "changed"
    assert sig.title == "Pricing page changed: Acme Pricing"
    assert sig.meta["kind"] == "page_diff" and sig.meta["diff_mode"] == "full"
    assert sig.meta["before_snapshot_id"] == snaps[0].id
    assert sig.meta["after_snapshot_id"] == snaps[1].id
    assert sig.published_at == snaps[1].captured_at
    assert sig.canonical_url.startswith(f"{URL}#diff-")
    assert "Team $5 per user/month" in pd.after_text(sig.raw_text)

    # Redesign with the same prices: snapshot marked, no signal.
    # (v2 raised Team to $5, so the redesign keeps $5 here: really the same prices.)
    same = fixture("pricing_redesign_same_prices.html").decode()
    web.add(URL, same.replace("$4 per user/month", "$5 per user/month"))
    redesign = run_collection(
        session_factory=collect_db, client=make_client(), settings=make_settings()
    ).sources[0]
    assert redesign.new == 0 and redesign.notes[0].startswith("restructured")
    last = _rows(collect_db, PageSnapshot, pricing)[-1]
    assert last.meta["page_restructured"] is True and last.meta["changed_line_ratio"] > 0.5


def test_collector_redesign_with_price_change_creates_price_lines_signal(
    pricing, collect_db, web, make_client
):
    _collect(collect_db, web, make_client, "pricing_v1")
    report = _collect(collect_db, web, make_client, "pricing_redesign")
    assert report.new == 1
    (sig,) = _rows(collect_db, Signal, pricing)
    assert sig.meta["diff_mode"] == "price_lines" and sig.meta["page_restructured"] is True
    assert "page was redesigned" in sig.raw_text
    assert pd.after_text(sig.raw_text) == "Enterprise $19 per user/month"


def test_collector_small_noise_creates_no_signal_but_accumulates(
    pricing, collect_db, web, make_client
):
    _collect(collect_db, web, make_client, "pricing_v1")
    v1 = fixture("pricing_v1.html").decode()
    web.add(URL, v1.replace("Can I change plans later?", "Can I switch plans later?"))
    report = run_collection(
        session_factory=collect_db, client=make_client(), settings=make_settings()
    ).sources[0]
    assert report.new == 0 and report.notes[0].startswith("noise")
    snaps = _rows(collect_db, PageSnapshot, pricing)
    assert snaps[-1].meta["diff_kind"] == "noise"
    # The next version is diffed against the baseline, not the noise snapshot.
    v2 = fixture("pricing_v2.html").decode()
    web.add(URL, v2.replace("Can I change plans later?", "Can I switch plans later?"))
    run_collection(session_factory=collect_db, client=make_client(), settings=make_settings())
    (sig,) = _rows(collect_db, Signal, pricing)
    assert sig.meta["before_snapshot_id"] == snaps[0].id
    assert "Can I switch plans later?" in sig.raw_text  # the earlier small edit is included


def test_js_shell_fails_the_source_without_a_snapshot(pricing, collect_db, web, make_client):
    report = _collect(collect_db, web, make_client, "pricing_js_shell")
    assert report.status == "failed" and "JavaScript" in (report.error or "")
    assert _rows(collect_db, PageSnapshot, pricing) == []


# ---------- Wayback ----------

NOW = at("2026-10-21")


def test_cdx_parse_and_closest_pick_in_date_order():
    rows = [
        ["timestamp", "statuscode", "mimetype"],
        ["20260715000000", "200", "text/html"],
        ["20251020120000", "200", "text/html"],
        ["20260420000000", "200", "text/html"],
    ]
    caps = parse_cdx(rows)
    assert [c.timestamp for c in caps] == ["20251020120000", "20260420000000", "20260715000000"]
    targets = [NOW - timedelta(days=d) for d in (365, 182, 91)]
    picks, missing = pick_closest(caps, targets, timedelta(days=31))
    assert [picks[t].timestamp for t in sorted(picks)] == [c.timestamp for c in caps]
    assert missing == []
    assert parse_timestamp("2026") == at("2026-01-01")


def test_missing_capture_outside_the_window_is_reported():
    caps = [Capture("20260715000000", at("2026-07-15"))]
    targets = [NOW - timedelta(days=d) for d in (365, 182, 91)]
    picks, missing = pick_closest(caps, targets, timedelta(days=31))
    assert len(picks) == 1 and [m.date().isoformat() for m in missing] == [
        "2025-10-21", "2026-04-22",
    ]  # fmt: skip
    assert parse_cdx([]) == []


def _serve_wayback(web, captures: dict[str, str], today: str):
    since = NOW - timedelta(days=365 + 31)
    rows = [["timestamp", "statuscode", "mimetype"]] + [[ts, "200", "text/html"] for ts in captures]
    web.add(cdx_url(CFG, URL, since, NOW), json.dumps(rows))
    for ts, page in captures.items():
        web.add(CFG.wayback.snapshot_url.format(timestamp=ts, url=URL), fixture(f"{page}.html"))
    web.add(URL, fixture(f"{today}.html"))


def test_backfill_stores_snapshots_diffs_in_order_and_dates_signals(
    pricing, collect_db, web, make_client
):
    _serve_wayback(
        web,
        {
            "20251020000000": "pricing_v1",  # ~12 months
            "20260420000000": "pricing_v1_reorder",  # ~6 months: layout only
            "20260720000000": "pricing_v2",  # ~3 months: real change
        },
        today="pricing_redesign_same_prices",
    )
    client = make_client()
    ref = _ref(collect_db, pricing)
    report = backfill_source(collect_db, client, ref, CFG, run_id=_run_id(collect_db), now=NOW)

    assert [s["captured"] for s in report.snapshots] == [
        "2025-10-20", "2026-04-20", "2026-07-20", "2026-10-21",
    ]  # fmt: skip
    assert report.missing == [] and report.rejected == []
    assert [(p["before"], p["after"], p["kind"]) for p in report.pairs] == [
        ("2025-10-20", "2026-04-20", "noise"),
        ("2025-10-20", "2026-07-20", "changed"),  # vs the last non-noise snapshot
        ("2026-07-20", "2026-10-21", "restructured"),
    ]
    # The redesign still carries a price change ($5 → $4 for Team): price-lines signal.
    assert report.pairs[2]["changed_line_ratio"] > 0.5 and report.pairs[2]["signal"]
    assert report.pairs[2]["diff_mode"] == "price_lines"
    first, second = _rows(collect_db, Signal, pricing)
    assert first.published_at == at("2026-07-20")  # dated to the later snapshot
    assert first.meta["before_origin"] == "wayback" and first.meta["after_origin"] == "wayback"
    assert second.published_at == NOW and second.meta["diff_mode"] == "price_lines"
    assert pd.after_text(second.raw_text) == "Team $4 per user/month"
    snaps = _rows(collect_db, PageSnapshot, pricing)
    assert [s.meta.get("origin") for s in snaps] == ["wayback", "wayback", "wayback", "live"]
    assert snaps[0].meta["wayback_timestamp"] == "20251020000000"
    assert snaps[-1].meta["page_restructured"] is True

    # Re-run: archived captures reused (no fetch), no new snapshots or signals.
    fetched = web.count(CFG.wayback.snapshot_url.format(timestamp="20251020000000", url=URL))
    again = backfill_source(collect_db, client, ref, CFG, run_id=_run_id(collect_db), now=NOW)
    assert all(s["reused"] for s in again.snapshots)
    assert again.signals_new == 0 and again.signals_existing == 2
    assert len(_rows(collect_db, PageSnapshot, pricing)) == 4
    assert (
        web.count(CFG.wayback.snapshot_url.format(timestamp="20251020000000", url=URL)) == fetched
    )


def test_backfill_reports_missing_and_misrendered_captures_without_diffing_them(
    pricing, collect_db, web, make_client
):
    _serve_wayback(
        web,
        {"20260420000000": "pricing_js_shell", "20260720000000": "pricing_v1"},
        today="pricing_v2",
    )
    ref = _ref(collect_db, pricing)
    report = backfill_source(
        collect_db, make_client(), ref, CFG, run_id=_run_id(collect_db), now=NOW
    )
    assert [m["target"] for m in report.missing] == ["2025-10-21"]
    assert len(report.rejected) == 1 and report.rejected[0]["captured"] == "2026-04-20"
    assert "JavaScript" in report.rejected[0]["reason"]
    # Only the usable capture and today were stored and compared.
    assert [p["before"] for p in report.pairs] == ["2026-07-20"]
    assert len(_rows(collect_db, PageSnapshot, pricing)) == 2


def test_archived_page_much_shorter_than_today_is_rejected():
    today = text("pricing_v1")
    short = "\n".join(today.splitlines()[:6])
    reason = __import__("app.pipeline.wayback", fromlist=["x"]).check_archived(short, today, CFG)
    assert reason is not None and reason.startswith("rendered differently")


def test_run_backfill_records_a_run_and_survives_a_failing_source(
    pricing, collect_db, web, make_client
):
    # No routes at all: today's page 404s → that source fails, the run is recorded.
    run_id, status, reports = run_backfill(collect_db, make_client(), cfg=CFG, now=NOW)
    assert status is RunStatus.FAILED and reports[0].status == "failed"
    with collect_db() as s:
        from app.db.models import PipelineRun

        run = s.get_one(PipelineRun, run_id)
        assert run.counts["kind"] == "wayback" and run.error


def _ref(db, source_id):
    from app.pipeline.collect import _ref as to_ref

    with db() as s:
        return to_ref(s.get_one(Source, source_id))


def _run_id(db) -> int:
    from app.db.enums import RunTrigger
    from app.db.models import PipelineRun

    with db.begin() as s:
        run = PipelineRun(trigger=RunTrigger.BACKFILL)
        s.add(run)
        s.flush()
        return run.id
