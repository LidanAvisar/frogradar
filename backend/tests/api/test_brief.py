from datetime import date, timedelta

from app.db.enums import FetchMethod, Relationship, ReportKind, RunStatus, RunTrigger, Verification
from app.db.models import PipelineRun, Report
from app.pipeline.scope import ranked
from tests.api.conftest import NOW


def brief(client, **params):
    r = client.get("/api/brief", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def ranking(data):
    """Without a daily brief the top story is the top-ranked signal; developments follow it."""
    story = [data["top_story"]["signal"]["id"]] if data["top_story"] else []
    return story + [d["id"] for d in data["developments"]]


def test_top_ranked_by_impact_times_confidence(client, build):
    a = build.signal("a", impact=3, confidence=0.9)  # 2.7
    b = build.signal("b", impact=5, confidence=0.6)  # 3.0
    c = build.signal("c", impact=4, confidence=0.8)  # 3.2
    d = build.signal("d", impact=2, confidence=0.9)  # 1.8

    data = brief(client)

    assert ranking(data) == [c.id, b.id, a.id, d.id]
    assert data["top_story"]["source"] == "top_signal"
    assert [t["rank"] for t in data["developments"]] == [1, 2, 3]


def test_top_tie_broken_by_newest(client, build):
    old = build.signal("old", hours_ago=30)
    new = build.signal("new", hours_ago=3)
    assert ranking(brief(client)) == [new.id, old.id]


def test_top_limit_and_window(client, build):
    for i in range(7):
        build.signal(f"in {i}", hours_ago=24 * i + 1)
    outside = build.signal("outside", hours_ago=24 * 8, impact=5)

    data = brief(client, days=7, limit=4)
    assert len(data["developments"]) == 4
    assert outside.id not in ranking(data)
    assert data["window"]["days"] == 7
    assert outside.id in ranking(brief(client, days=10, limit=20))


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

    ids = set(ranking(brief(client)))
    assert ids == {shown.id, partial.id, canonical.id}


def test_mission_findings_never_rank_or_count_as_new(client, build):
    """Approved research findings have no publish date: by fetch time they would look like
    today's top news (the same story twice in the Top 5). They stay in the signal feed (D82)."""
    news = build.signal("news", impact=3, confidence=0.8)
    finding = build.signal("finding", impact=5, confidence=0.85, published=False,
                           fetched_hours_ago=1, meta={"kind": "mission"})  # fmt: skip
    finding.source_id = None
    build.s.flush()

    data = brief(client)
    assert ranking(data) == [news.id]
    assert finding.id not in [i["id"] for i in data["since_yesterday"]]
    # One ranking for the Brief, the daily brief's input and MCP whats_new.
    since = NOW - timedelta(days=7)
    assert build.s.scalars(ranked(since, NOW)).all() == [news]
    # Still intelligence: the signal feed and detail show it.
    items = client.get("/api/signals").json()["items"]
    assert finding.id in [i["id"] for i in items]
    assert client.get(f"/api/signals/{finding.id}").status_code == 200
    # A date window (the Brief's tiles link to one) is about news: undated research is left out.
    for params in ({"window": "24h"}, {"since": (NOW - timedelta(days=2)).isoformat()}):
        windowed = client.get("/api/signals", params=params).json()["items"]
        assert [i["id"] for i in windowed] == [news.id]


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
    assert items[0]["changes_source"] == "parser"
    assert items[1]["changes"] == [] and items[1]["changes_source"] is None


def test_pricing_watch_prefers_verified_model_changes_and_display_title(client, build):
    page = build.source("A Pricing", method=FetchMethod.PAGE_DIFF)
    verified = [
        {"label": "Cloud > Pro Edition", "before": "$1,620 / year", "after": "$1,950 / year"}
    ]
    sig = build.signal(
        "Pricing page changed: A Pricing", source=page, raw_text=_diff("$1,000", "$2,000"),
        takeaway="A raises Pro: an opening for JFrog Artifactory.",
        meta={"kind": "page_diff", "price_changes": verified,
              "display_title": "A raises Cloud Pro to $1,950 a year"},
    )  # fmt: skip
    (item,) = brief(client)["pricing_watch"]
    assert item["changes"] == verified and item["changes_source"] == "verified_llm"
    assert item["signal"]["title"] == "A raises Cloud Pro to $1,950 a year"
    assert item["signal"]["original_title"] == sig.title
    assert item["signal"]["takeaway"] == "A raises Pro: an opening for JFrog Artifactory."
    build.signal("Bigger news", impact=5, hours_ago=1)  # becomes the top story
    build.signal("Next news", impact=5, hours_ago=1.5)  # takes the only developments slot
    (new,) = [i for i in brief(client, limit=1)["since_yesterday"] if i["id"] == sig.id]
    assert new["title"] == "A raises Cloud Pro to $1,950 a year"


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
    assert data["developments"] == [] and data["since_yesterday"] == []
    assert data["pricing_watch"] == [] and data["top_story"] is None
    assert data["pipeline"]["last_run"] is None
    assert data["stats"] == {
        "new_signals": 0, "high_impact": 0, "price_changes": 0, "price_change_days": 7,
    }  # fmt: skip


def test_invalid_params(client):
    assert client.get("/api/brief", params={"days": 0}).status_code == 422
    assert client.get("/api/brief", params={"limit": 50}).status_code == 422


def test_since_yesterday_leaves_out_what_is_shown_but_stats_count_it(client, build):
    story = build.signal("story", hours_ago=2, impact=5)
    dev = build.signal("development", hours_ago=2.5, impact=4)
    only_new = [build.signal(f"new {i}", hours_ago=3, impact=2) for i in range(2)]
    build.signal("old", hours_ago=24 * 3, impact=1)
    page = build.source("P", method=FetchMethod.PAGE_DIFF)
    build.signal("price", source=page, hours_ago=24 * 3, impact=3, meta={"kind": "page_diff"})
    build.signal("old price", source=page, hours_ago=24 * 9, impact=3, meta={"kind": "page_diff"})

    data = brief(client, limit=1)

    assert ranking(data) == [story.id, dev.id]
    assert {i["id"] for i in data["since_yesterday"]} == {s.id for s in only_new}
    assert data["stats"] == {
        "new_signals": 4, "high_impact": 2, "price_changes": 1, "price_change_days": 7,
    }  # fmt: skip


def test_pricing_watch_ordered_by_impact_then_date(client, build):
    meta = {"kind": "page_diff"}
    pages = [build.source(f"P{i}", method=FetchMethod.PAGE_DIFF) for i in range(3)]
    low_new = build.signal("low new", source=pages[0], hours_ago=1, impact=2, meta=meta)
    high_old = build.signal("high old", source=pages[1], hours_ago=24 * 20, impact=4, meta=meta)
    high_new = build.signal("high new", source=pages[2], hours_ago=24 * 2, impact=4, meta=meta)

    ids = [i["signal"]["id"] for i in brief(client)["pricing_watch"]]
    assert ids == [high_new.id, high_old.id, low_new.id]


def _story(**rows):
    return {key: {"text": text, "signal_ids": ids} for key, (text, ids) in rows.items()}


def test_top_story_from_the_daily_brief_with_company_products_and_sources(client, build):
    jfrog = build.entity("jfrog", "JFrog", relationship=Relationship.SELF, is_self=True)
    build.entity("jfrog-curation", "JFrog Curation", parent=jfrog)
    build.entity("jfrog-advanced-security", "JFrog Advanced Security", parent=jfrog)
    build.entity(
        "jfrog-pipelines", "JFrog Pipelines", parent=jfrog, discontinued_on=date(2026, 5, 1)
    )
    github = build.entity("github", "GitHub")
    firewall = build.entity("github-firewall", "GitHub package firewall", parent=github)
    a = build.signal("GH firewall", impact=3)
    b = build.signal("GH license", impact=3)
    top = build.signal("Ranked first", impact=5)
    build.link(a, firewall)
    build.link(b, github)
    build.s.add(Report(kind=ReportKind.DAILY_BRIEF, created_at=NOW, content={"top_story": _story(
        title=("GitHub adds a package firewall that overlaps with Curation", [a.id]),
        what_happened=("GitHub previewed an npm-only package firewall.", [a.id, b.id]),
        why_it_matters_for_jfrog=(
            "It overlaps with Curation; GitHub Advanced Security and JFrog Pipelines are not ours.",
            [a.id]),
        suggested_move=("Sales: add the npm-only limit to the Curation battlecard.", [a.id]),
    )}))  # fmt: skip
    build.s.flush()

    data = brief(client)
    story = data["top_story"]
    assert story["source"] == "daily_brief"
    assert story["title"] == "GitHub adds a package firewall that overlaps with Curation"
    assert [c["id"] for c in story["what_happened"]["citations"]] == [a.id, b.id]
    assert story["suggested_move"]["text"].startswith("Sales:")
    assert story["signal"]["id"] == a.id and story["company"] == "GitHub"
    assert story["affects"] == ["JFrog Curation"]  # other vendor's product, discontinued: skipped
    assert story["signal_ids"] == [a.id, b.id]
    # Developments leave out the story's signals; the top-ranked signal is listed there.
    assert [d["id"] for d in data["developments"]] == [top.id]


def test_story_row_with_an_unusable_signal_falls_back_to_the_top_signal(client, build):
    a = build.signal("A", impact=3, summary="A summary of A.", takeaway="Matters for Curation.")
    gone = build.signal("Pending re-enrichment", enriched=False)
    lead = build.signal("Lead", impact=5, summary="Lead summary.", takeaway="Lead takeaway.")
    build.s.add(Report(kind=ReportKind.DAILY_BRIEF, created_at=NOW, content={"top_story": _story(
        title=("Title", [a.id]), what_happened=("What.", [a.id, gone.id]),
        why_it_matters_for_jfrog=("Why.", [a.id]),
    )}))  # fmt: skip
    build.s.flush()

    story = brief(client)["top_story"]
    assert story["source"] == "top_signal" and story["signal"]["id"] == lead.id
    assert story["title"] == "Lead"
    assert story["what_happened"]["text"] == "Lead summary."
    assert story["why_it_matters"]["text"] == "Lead takeaway."
    assert story["suggested_move"] is None
    assert [c["id"] for c in story["title_citations"]] == [lead.id]


def test_dropped_suggested_move_keeps_the_story(client, build):
    a = build.signal("A")
    build.s.add(Report(kind=ReportKind.DAILY_BRIEF, created_at=NOW, content={"top_story": _story(
        title=("Title", [a.id]), what_happened=("What.", [a.id]),
        why_it_matters_for_jfrog=("Why.", [a.id]),
    )}))  # fmt: skip
    build.s.flush()
    story = brief(client)["top_story"]
    assert story["source"] == "daily_brief" and story["suggested_move"] is None


def _daily_brief(build, created_at, title: str, signal_id: int) -> None:  # noqa: ANN001
    story = _story(title=(title, [signal_id]), what_happened=("What.", [signal_id]),
                   why_it_matters_for_jfrog=("Why.", [signal_id]))  # fmt: skip
    build.s.add(Report(kind=ReportKind.DAILY_BRIEF, created_at=created_at,
                       content={"top_story": story}))  # fmt: skip


def test_old_data_shows_the_latest_available_day_with_its_date(client, build):
    """Nothing in the 7-day window (e.g. only older seed data): the Brief is anchored on the newest
    rankable signal and says so (stale), instead of an empty page or old news as today's (D83)."""
    older = build.signal("older", hours_ago=24 * 30, impact=3)
    newest = build.signal("newest", hours_ago=24 * 20, impact=4)
    same_day = build.signal("same day", hours_ago=24 * 20 + 3, impact=2)
    _daily_brief(build, NOW - timedelta(days=20), "Old story", newest.id)
    build.s.flush()

    data = brief(client)
    assert data["stale"] is True
    assert data["as_of"].startswith((NOW - timedelta(days=20)).date().isoformat())
    assert data["generated_at"].startswith(NOW.date().isoformat())
    assert ranking(data) == [newest.id, same_day.id]  # the 7 days up to the anchor
    assert older.id not in ranking(data)
    # The daily brief of that day is the top story (it is within 24h of the anchor).
    assert data["top_story"]["source"] == "daily_brief"
    # "Since yesterday" is the anchor's day.
    assert {i["id"] for i in data["since_yesterday"]} <= {newest.id, same_day.id}


def test_a_daily_brief_older_than_a_day_is_never_todays_top_story(client, build):
    a = build.signal("fresh", hours_ago=2, impact=4)
    _daily_brief(build, NOW - timedelta(days=3), "Three days old", a.id)
    build.s.flush()

    data = brief(client)
    assert data["stale"] is False
    assert data["top_story"]["source"] == "top_signal"
