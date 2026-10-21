from datetime import timedelta

from app.db.enums import FetchMethod, ReportKind, RunStatus, RunTrigger, Verification
from app.db.models import PipelineRun, Report
from tests.api.conftest import NOW


def brief(client, **params):
    r = client.get("/api/brief", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_top_ranked_by_impact_times_confidence(client, build):
    a = build.signal("a", impact=3, confidence=0.9)  # 2.7
    b = build.signal("b", impact=5, confidence=0.6)  # 3.0
    c = build.signal("c", impact=4, confidence=0.8)  # 3.2
    d = build.signal("d", impact=2, confidence=0.9)  # 1.8

    top = brief(client)["top"]

    assert [t["id"] for t in top] == [c.id, b.id, a.id, d.id]
    assert [t["rank"] for t in top] == [1, 2, 3, 4]


def test_top_tie_broken_by_newest(client, build):
    old = build.signal("old", hours_ago=30)
    new = build.signal("new", hours_ago=3)
    assert [t["id"] for t in brief(client)["top"]] == [new.id, old.id]


def test_top_limit_and_window(client, build):
    for i in range(7):
        build.signal(f"in {i}", hours_ago=24 * i + 1)
    outside = build.signal("outside", hours_ago=24 * 8, impact=5)

    data = brief(client, days=7, limit=5)
    assert len(data["top"]) == 5
    assert outside.id not in [t["id"] for t in data["top"]]
    assert data["window"]["days"] == 7
    assert outside.id in [t["id"] for t in brief(client, days=10, limit=20)["top"]]


def test_top_excludes_unshowable_signals(client, build):
    shown = build.signal("shown")
    partial = build.signal("partial", verification=Verification.PARTIAL)
    build.signal("irrelevant", relevant=False)
    build.signal("not enriched", enriched=False)
    build.signal("pending", verification=Verification.PENDING)
    build.signal("unsupported", verification=Verification.UNSUPPORTED)
    build.signal("inactive source", source=build.source(active=False))
    canonical = build.signal("canonical")
    dup = build.signal("duplicate", impact=5)
    dup.dedup_cluster_id = canonical.id
    canonical.dedup_cluster_id = canonical.id
    build.s.flush()

    ids = {t["id"] for t in brief(client)["top"]}
    assert ids == {shown.id, partial.id, canonical.id}


def test_since_yesterday_uses_publish_date(client, build):
    # Pending verification: new, but never in the Top 5, so the list isn't deduped here.
    pending = {"verification": Verification.PENDING}
    fresh = build.signal("fresh", hours_ago=5, impact=2, **pending)
    high = build.signal("fresh high", hours_ago=20, impact=5, **pending)
    # Backfilled: published three weeks ago but fetched an hour ago. Not new.
    backfilled = build.signal("backfilled", hours_ago=24 * 21, fetched_hours_ago=1, **pending)
    # No publish date: falls back to fetched_at.
    undated = build.signal("undated", published=False, fetched_hours_ago=3, **pending)
    old_undated = build.signal("old undated", published=False, fetched_hours_ago=30, **pending)
    build.signal("yesterday-ish", hours_ago=25, **pending)
    build.signal("irrelevant", hours_ago=1, relevant=False, **pending)

    items = brief(client)["since_yesterday"]
    ids = [i["id"] for i in items]
    assert backfilled.id not in ids
    assert old_undated.id not in ids
    assert set(ids) == {fresh.id, high.id, undated.id}
    assert ids[0] == high.id  # highest impact first
    assert items[0]["impact"] == 5


def test_filtered_not_relevant_count(client, build):
    build.signal("a", relevant=False, enriched=False)
    build.signal("b", relevant=False, enriched=False, hours_ago=48)
    build.signal("too old", relevant=False, enriched=False, hours_ago=24 * 9)
    build.signal("inactive", relevant=False, enriched=False, source=build.source(active=False))
    build.signal("relevant")
    assert brief(client)["filtered_not_relevant"] == 2


def _diff(price_before: str, price_after: str) -> str:
    return (
        "Page: P\n\n## Change 1 of 1\n[before]\n"
        f"[Plans > Pro] Starting at {price_before} / year\n[after]\n"
        f"[Plans > Pro] Starting at {price_after} / year\n"
    )


def test_pricing_watch_latest_diff_per_page(client, build):
    page_a = build.source("A Pricing", method=FetchMethod.PAGE_DIFF)
    page_b = build.source("B Pricing", method=FetchMethod.PAGE_DIFF)
    meta = {
        "kind": "page_diff",
        "before_origin": "wayback",
        "before_captured_at": (NOW - timedelta(days=60)).isoformat(),
        "after_captured_at": (NOW - timedelta(hours=2)).isoformat(),
    }
    build.signal("A old", source=page_a, hours_ago=24 * 30, meta=meta)
    a_new = build.signal(
        "A new", source=page_a, hours_ago=2, meta=meta, raw_text=_diff("$1,620", "$1,950")
    )
    b = build.signal("B", source=page_b, hours_ago=24 * 10, meta=meta)
    build.signal("article")
    build.signal("too old", source=page_b, hours_ago=24 * 120, meta=meta)

    items = brief(client)["pricing_watch"]

    assert [i["signal"]["id"] for i in items] == [a_new.id, b.id]
    assert items[0]["changes"] == [
        {"label": "Plans > Pro", "before": "$1,620 / year", "after": "$1,950 / year"}
    ]
    assert items[0]["signal"]["kind"] == "page_diff"
    assert items[0]["signal"]["page_diff"]["before_origin"] == "wayback"
    assert items[1]["changes"] == []


def test_pipeline_info_skips_eval_runs(client, build):
    s = build.s
    for hours, counts in ((5, {"graph": []}), (1, {"kind": "eval_pipeline"})):
        s.add(
            PipelineRun(
                trigger=RunTrigger.MANUAL,
                status=RunStatus.SUCCEEDED,
                started_at=NOW - timedelta(hours=hours),
                counts=counts,
            )  # fmt: skip
        )
    s.flush()
    build.source("feed 1")
    build.source("feed 2", method=FetchMethod.GITHUB)
    build.source("off", active=False)
    build.source("page", method=FetchMethod.PAGE_DIFF)

    info = brief(client)["pipeline"]
    assert info["last_run"]["started_at"].startswith("2026-10-21T03:00")
    assert info["last_run"]["status"] == "succeeded"
    assert info["active_feeds"] == 2
    assert info["watched_pages"] == 1


def test_no_runs_and_empty_db(client):
    data = brief(client)
    assert data["top"] == [] and data["since_yesterday"] == [] and data["pricing_watch"] == []
    assert data["pipeline"]["last_run"] is None
    assert data["headline"] is None and data["overview"] is None


def test_headline_from_latest_daily_brief(client, build):
    s = build.s
    s.add(Report(kind=ReportKind.DAILY_BRIEF, content={"headline": "Old", "overview": "o"},
                 created_at=NOW - timedelta(days=1)))  # fmt: skip
    s.add(Report(kind=ReportKind.DAILY_BRIEF, content={"headline": "Sonatype reprices Nexus"},
                 created_at=NOW - timedelta(hours=1)))  # fmt: skip
    s.add(Report(kind=ReportKind.BATTLECARD, content={"headline": "Not a brief"}, created_at=NOW))
    s.flush()

    data = brief(client)
    assert data["headline"] == "Sonatype reprices Nexus"
    assert data["overview"] is None


def test_invalid_params(client):
    assert client.get("/api/brief", params={"days": 0}).status_code == 422
    assert client.get("/api/brief", params={"limit": 50}).status_code == 422


def test_since_yesterday_leaves_out_top_but_counts_it(client, build):
    in_top = build.signal("in top", hours_ago=2, impact=5)
    only_new = [build.signal(f"new {i}", hours_ago=3, impact=2) for i in range(2)]
    build.signal("old", hours_ago=24 * 3, impact=1)

    data = brief(client, limit=1)

    assert [t["id"] for t in data["top"]] == [in_top.id]
    assert {i["id"] for i in data["since_yesterday"]} == {s.id for s in only_new}
    assert data["since_yesterday_total"] == 3
    assert data["since_yesterday_high_impact"] == 1


def test_pricing_watch_ordered_by_impact_then_date(client, build):
    meta = {"kind": "page_diff"}
    pages = [build.source(f"P{i}", method=FetchMethod.PAGE_DIFF) for i in range(3)]
    low_new = build.signal("low new", source=pages[0], hours_ago=1, impact=2, meta=meta)
    high_old = build.signal("high old", source=pages[1], hours_ago=24 * 20, impact=4, meta=meta)
    high_new = build.signal("high new", source=pages[2], hours_ago=24 * 2, impact=4, meta=meta)

    ids = [i["signal"]["id"] for i in brief(client)["pricing_watch"]]
    assert ids == [high_new.id, high_old.id, low_new.id]
