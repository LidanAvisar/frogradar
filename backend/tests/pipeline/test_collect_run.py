"""Full collection runs against a real (throwaway) Postgres, with the web faked."""

import io
import json
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.db.enums import EntityType, FetchMethod, Relationship, RunStatus, RunTrigger, SourceKind
from app.db.models import Entity, PipelineRun, Signal, Source, signal_entities
from app.pipeline.collect import parse_since, print_report, run_collection
from tests.pipeline.conftest import fixture, make_settings

pytestmark = pytest.mark.db

SINCE = datetime(2026, 7, 22, tzinfo=UTC)
FEED = "https://blog.acme.test/feed"
ARTICLE = "https://blog.acme.test/firewall-2/?utm_source=rss&utm_medium=feed"
RELEASES = "https://api.github.com/repos/acme/acme-cli/releases"


@pytest.fixture
def seeded(collect_db):
    """Acme with RSS, GitHub and a stub (web) source; an industry feed without an entity."""
    with collect_db.begin() as s:
        acme = Entity(
            type=EntityType.COMPANY, name="Acme", slug="acme", relationship=Relationship.COMPETITOR
        )
        s.add(acme)
        s.flush()

        def src(name, url, method, kind=SourceKind.OFFICIAL, entity=acme, **kw):
            row = Source(
                name=name, url=url, kind=kind, fetch_method=method, trust_score=0.9,
                entity_id=entity.id if entity else None, **kw,
            )  # fmt: skip
            s.add(row)
            return row

        rows = {
            "rss": src("Acme Blog", FEED, FetchMethod.RSS),
            "gh": src(
                "Acme GitHub",
                "https://github.com/acme",
                FetchMethod.GITHUB,
                kind=SourceKind.GITHUB,
                options={"repos": ["acme-cli"]},
            ),  # fmt: skip
            "pricing": src("Acme Pricing", "https://acme.test/pricing", FetchMethod.WEB),
            "news": src(
                "Industry News",
                "https://news.test/feed",
                FetchMethod.RSS,
                kind=SourceKind.PRESS,
                entity=None,
            ),  # fmt: skip
            "inactive": src("Old Feed", "https://old.test/feed", FetchMethod.RSS, is_active=False),
        }
        s.flush()
        ids = {k: v.id for k, v in rows.items()} | {"acme": acme.id}
    return ids


@pytest.fixture
def run(collect_db, web, make_client):
    def _run(**kw):
        kw.setdefault("since", SINCE)
        return run_collection(
            session_factory=collect_db, client=make_client(), settings=make_settings(), **kw
        )

    return _run


def _serve_all(web):
    web.add(FEED, fixture("blog_rss.xml"), etag='"v1"')
    web.add(ARTICLE, fixture("article_firewall.html"))
    web.add(RELEASES, fixture("github_releases_page1.json"))
    web.add("https://news.test/feed", fixture("changelog_atom.xml"))


def _signals(db, source_id=None):
    with db() as s:
        q = select(Signal).order_by(Signal.id)
        if source_id is not None:
            q = q.where(Signal.source_id == source_id)
        return list(s.scalars(q))


def test_run_stores_signals_and_records_the_run(seeded, collect_db, web, run):
    _serve_all(web)

    report = run()

    by_name = {r.name: r for r in report.sources}
    assert "Old Feed" not in by_name  # inactive sources are not collected
    assert (by_name["Acme Blog"].new, by_name["Acme GitHub"].new, by_name["Industry News"].new) == (
        2, 3, 1,
    )  # fmt: skip
    assert by_name["Acme Pricing"].status == "skipped"
    assert report.status is RunStatus.SUCCEEDED  # skipped stubs are not failures

    blog = _signals(collect_db, seeded["rss"])
    assert {s.canonical_url for s in blog} == {
        "https://blog.acme.test/firewall-2",
        "https://blog.acme.test/sbom-export",
    }
    assert all(s.run_id == report.run_id for s in blog)
    gh = _signals(collect_db, seeded["gh"])
    assert {s.meta["tag"]: s.meta["prerelease"] for s in gh} == {
        "v3.0.0-rc.1": True, "v2.9.0": False, "v2.8.0": False,
    }  # fmt: skip

    with collect_db() as s:
        links = s.execute(select(signal_entities)).all()
        run_row = s.get(PipelineRun, report.run_id)
        sources = {src.id: src for src in s.scalars(select(Source))}
    # Acme sources link their entity as primary; the industry feed has no entity yet.
    assert len(links) == 5 and all(link.is_primary for link in links)
    assert {link.entity_id for link in links} == {seeded["acme"]}
    assert run_row.status is RunStatus.SUCCEEDED and run_row.trigger is RunTrigger.MANUAL
    assert run_row.finished_at is not None
    counts = run_row.counts
    assert counts["totals"]["new"] == 6
    assert counts["sources"][str(seeded["rss"])]["history"] == "full window"
    assert counts["sources"][str(seeded["news"])]["history"] == "only back to 2026-10-18"
    assert sources[seeded["rss"]].http_etag == '"v1"'
    assert sources[seeded["rss"]].last_fetched_at is not None
    assert sources[seeded["pricing"]].last_fetched_at is None

    out = io.StringIO()
    print_report(report, out)
    assert "Acme Blog" in out.getvalue() and "6 new" in out.getvalue()


def test_second_run_counts_duplicates_and_skips_known_articles(seeded, collect_db, web, run):
    _serve_all(web)
    run()
    article_fetches = web.count(ARTICLE)

    report = run()

    blog = next(r for r in report.sources if r.name == "Acme Blog")
    assert (blog.new, blog.updated, blog.duplicate) == (0, 0, 2)
    assert web.count(ARTICLE) == article_fetches  # stored with article text: not re-fetched
    assert len(_signals(collect_db)) == 6


def test_changed_text_updates_the_signal(seeded, collect_db, web, run):
    _serve_all(web)
    first = run()
    releases = json.loads(fixture("github_releases_page1.json"))
    releases[2]["body"] = "## Changes in v2.9.0\n- SBOM support\n- edited: security fix added"
    web.add(RELEASES, json.dumps(releases))

    second = run()

    gh = next(r for r in second.sources if r.name == "Acme GitHub")
    assert (gh.new, gh.updated, gh.duplicate) == (0, 1, 2)
    [sig] = [s for s in _signals(collect_db, seeded["gh"]) if s.meta["tag"] == "v2.9.0"]
    assert "edited: security fix added" in sig.raw_text
    assert sig.meta["revisions"] == 1 and "last_changed_at" in sig.meta
    assert sig.meta["last_changed_run_id"] == second.run_id
    assert sig.run_id == first.run_id  # still credited to the run that first found it
    assert sig.enriched_at is None
    with collect_db() as s:
        assert s.get(PipelineRun, second.run_id).counts["totals"]["updated"] == 1


def test_same_content_from_another_source_is_a_duplicate(seeded, collect_db, web, run):
    _serve_all(web)
    web.add("https://news.test/feed", fixture("blog_rss.xml"))  # syndicates the Acme blog
    web.add("https://blog.acme.test/sbom-export", "")
    report = run()
    by_name = {r.name: r for r in report.sources}
    assert by_name["Acme Blog"].new == 2
    # Same title + text under the same URLs, but a different source: exact copies → duplicate.
    assert by_name["Industry News"].duplicate == 2 and by_name["Industry News"].new == 0


def test_failing_source_does_not_stop_the_run(seeded, collect_db, web, run):
    _serve_all(web)

    def broken(request):
        raise httpx.ConnectError("connection refused", request=request)

    web.routes[FEED] = broken
    web.add(RELEASES, json.dumps({"message": "API rate limit exceeded"}), status=403)

    report = run()

    by_name = {r.name: r for r in report.sources}
    assert by_name["Acme Blog"].status == "failed" and "ConnectError" in by_name["Acme Blog"].error
    assert (
        by_name["Acme GitHub"].status == "failed" and "GITHUB_TOKEN" in by_name["Acme GitHub"].error
    )
    assert by_name["Industry News"].new == 1  # the healthy source still ran
    assert report.status is RunStatus.PARTIAL
    with collect_db() as s:
        run_row = s.get(PipelineRun, report.run_id)
    assert run_row.status is RunStatus.PARTIAL
    assert "Acme Blog: " in run_row.error and "Acme GitHub: " in run_row.error
    assert run_row.counts["totals"]["sources_failed"] == 2


def test_robots_disallow_fails_only_that_source(seeded, web, run):
    _serve_all(web)
    web.add("https://news.test/robots.txt", "User-agent: *\nDisallow: /\n")
    report = run()
    news = next(r for r in report.sources if r.name == "Industry News")
    assert news.status == "failed" and "robots.txt" in news.error
    assert report.status is RunStatus.PARTIAL
    assert "https://news.test/feed" not in web.urls()


def test_all_sources_failing_marks_the_run_failed(seeded, web, run):
    report = run()  # nothing served: every feed 404s
    assert report.status is RunStatus.FAILED


def test_single_source_by_name_or_id_and_backfill_trigger(seeded, collect_db, web, run):
    _serve_all(web)
    report = run(source="acme blog", backfill=True)
    assert [r.name for r in report.sources] == ["Acme Blog"]
    report = run(source=str(seeded["inactive"]))  # explicitly selected inactive source still runs
    assert [r.name for r in report.sources] == ["Old Feed"]
    with collect_db() as s:
        triggers = [r.trigger for r in s.scalars(select(PipelineRun).order_by(PipelineRun.id))]
    assert triggers == [RunTrigger.BACKFILL, RunTrigger.MANUAL]
    with pytest.raises(SystemExit, match="no source"):
        run(source="does not exist")


def test_parse_since():
    now = datetime(2026, 10, 20, 12, tzinfo=UTC)
    assert parse_since("90d", now) == datetime(2026, 7, 22, 12, tzinfo=UTC)
    assert parse_since("2026-01-31") == datetime(2026, 1, 31, tzinfo=UTC)
