"""Discovery collectors (S11): Hacker News, GitHub search, CNCF landscape – offline fixtures."""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core.catalog import CNCFSettings, GitHubSearchSettings, HNQuerySet, HNSettings
from app.db.enums import FetchMethod
from app.pipeline.collectors import SourceRef
from app.pipeline.collectors.cncf import CNCFLandscapeCollector, additions, flatten, parse
from app.pipeline.collectors.discovery import github_repo
from app.pipeline.collectors.github import GitHubRateLimited
from app.pipeline.collectors.github_search import GitHubSearchCollector, NoiseFilter
from app.pipeline.collectors.hn import HNSearchCollector
from app.pipeline.radar.stars import velocity_from
from tests.pipeline.conftest import NOW, fixture

HN_API = "https://hn.algolia.com/api/v1/search_by_date"
GH_API = "https://api.github.com/search/repositories"
LANDSCAPE = "https://raw.githubusercontent.com/cncf/landscape/master/landscape.yml"
SINCE = NOW - timedelta(days=90)


def src(method: FetchMethod, url: str, id_: int = 1) -> SourceRef:
    return SourceRef(id=id_, name=f"{method} source", url=url, fetch_method=method, options={})


# ---------- Hacker News ----------


def hn_cfg(**kw) -> HNSettings:
    return HNSettings(
        keywords=HNQuerySet(min_points=15, queries=['"supply chain"', "npm"]),
        show_hn=HNQuerySet(min_points=10),
        **kw,
    )


def test_hn_parses_dedups_and_fetches_linked_pages(web, make_client):
    web.add(HN_API, fixture("hn_search.json"))
    web.add("https://pkgguard.dev/blog/launch", "<html><body><p>x</p></body></html>",
            content_type="text/html")  # fmt: skip
    web.add(
        "https://raw.githubusercontent.com/regshield/regshield/HEAD/README.md",
        "# Regshield\nA registry firewall.",
    )
    collector = HNSearchCollector(
        make_client(),
        hn_cfg(),
        now=lambda: NOW,
        extract=lambda html, url: "Pkgguard blocks bad installs.",
    )
    result = collector.collect(
        src(FetchMethod.HN_SEARCH, HN_API), since=SINCE, backfill=True, known={}
    )

    # 4 searches (2 keyword + 2 Show HN), all answered by the same fixture: deduped by objectID.
    assert [i.title for i in result.items] == [
        "Show HN: Pkgguard – block malicious npm packages before install",
        "Show HN: Regshield, an open-source registry firewall",
        "Ask HN: How do you vet new dependencies?",
    ]
    assert result.too_old == 1
    pkg, reg, ask = result.items
    assert pkg.canonical_url == "https://news.ycombinator.com/item?id=45100001"
    assert pkg.url == "https://pkgguard.dev/blog/launch"
    assert pkg.meta["kind"] == "discovery" and pkg.meta["discovery"] == "hn"
    assert pkg.meta["hn"]["points"] == 142 and pkg.meta["hn"]["tags"] == ["show_hn"]
    assert len(pkg.meta["hn"]["matched"]) == 4
    assert "Pkgguard blocks bad installs." in pkg.text and pkg.meta["text_source"] == "article"
    # Points never go into the text (a changed count must not look like an edited article).
    assert "142" not in pkg.text
    assert reg.meta["text_source"] == "readme" and "registry firewall" in reg.text
    assert "We built a proxy that sits in front of" in reg.text
    assert ask.url == "https://news.ycombinator.com/item?id=45100003"  # no link: HN discussion
    assert ask.meta["linked_url"] is None


def test_hn_query_parameters(web, make_client):
    web.add(HN_API, json.dumps({"hits": [], "nbPages": 0}))
    HNSearchCollector(make_client(), hn_cfg(), now=lambda: NOW).collect(
        src(FetchMethod.HN_SEARCH, HN_API), since=SINCE, backfill=False, known={}
    )
    queries = [parse_qs(urlsplit(u).query) for u in web.urls() if u.startswith(HN_API)]
    assert len(queries) == 4
    kw, show = queries[0], queries[2]
    assert kw["tags"] == ["story"] and kw["restrictSearchableAttributes"] == ["title"]
    assert kw["typoTolerance"] == ["false"] and kw["advancedSyntax"] == ["true"]
    assert kw["numericFilters"][0].startswith("points>=15,created_at_i>")
    assert show["tags"] == ["show_hn"] and "restrictSearchableAttributes" not in show
    assert show["numericFilters"][0].startswith("points>=10,")


def test_hn_known_items_are_not_refetched(web, make_client):
    web.add(HN_API, fixture("hn_search.json"))
    known = {
        "https://news.ycombinator.com/item?id=45100001": "article",
        "https://news.ycombinator.com/item?id=45100002": "readme",
    }
    result = HNSearchCollector(
        make_client(), hn_cfg(fetch_linked_page=False), now=lambda: NOW
    ).collect(src(FetchMethod.HN_SEARCH, HN_API), since=SINCE, backfill=False, known=known)
    assert [i.meta["hn"]["id"] for i in result.items] == [45100003]
    assert result.skipped_known == 2
    assert not any("pkgguard" in u for u in web.urls())


def test_github_repo_urls():
    assert github_repo("https://github.com/RegShield/regshield/tree/main/docs") == (
        "RegShield",
        "regshield",
    )
    assert github_repo("https://github.com/orgs/acme") is None
    assert github_repo("https://gitlab.com/a/b") is None
    assert github_repo("https://github.com/acme") is None


# ---------- GitHub search ----------


def gh_cfg(**kw) -> GitHubSearchSettings:
    values = {
        "queries": ["topic:sbom"],
        "min_stars": 30,
        "readme_chars": 100,
        "skip_name_patterns": {
            "awesome_list": r"(^|[-_.])awesome([-_.]|$)",
            "homework": r"homework",
        },
        "skip_description_patterns": {"tutorial": r"\btutorial\b"},
    }
    values.update(kw)
    return GitHubSearchSettings(**values)


def test_github_search_noise_filter_items_and_stars(web, make_client, clock):
    web.add(GH_API, fixture("github_search.json"))
    web.add(
        "https://raw.githubusercontent.com/regshield/regshield/HEAD/README.md",
        "# RegShield\n" + "x" * 500,
    )
    collector = GitHubSearchCollector(
        make_client(), gh_cfg(), now=lambda: NOW, clock=clock, sleep=clock.sleep
    )
    result = collector.collect(
        src(FetchMethod.GITHUB_SEARCH, GH_API), since=SINCE, backfill=True, known={}
    )

    assert result.skipped == {"awesome_list": 1, "fork": 1, "homework": 1, "archived": 1}
    [item] = result.items
    assert item.canonical_url == "https://github.com/regshield/regshield"
    assert item.title.startswith("New GitHub repository: RegShield/regshield")
    assert item.meta["repo"]["full_name"] == "regshield/regshield"
    assert item.meta["repo"]["stars_at_first_seen"] == 900
    assert "900" not in item.text  # stars are never in the text
    assert "Homepage: https://regshield.io" in item.text
    assert (
        item.meta["text_source"] == "readme"
        and len(item.text.split("README (excerpt):\n")[1]) == 100
    )
    assert item.published_at == datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    [snap] = result.star_snapshots
    assert (snap.repo, snap.stars, snap.forks) == ("regshield/regshield", 900, 40)

    q = parse_qs(urlsplit(web.urls()[0]).query)["q"][0]
    assert q == "topic:sbom created:>2026-07-22 stars:>=30"


def test_github_search_known_repo_still_gets_star_snapshot(web, make_client, clock):
    web.add(GH_API, fixture("github_search.json"))
    result = GitHubSearchCollector(
        make_client(), gh_cfg(), now=lambda: NOW, clock=clock, sleep=clock.sleep
    ).collect(
        src(FetchMethod.GITHUB_SEARCH, GH_API),
        since=SINCE,
        backfill=False,
        known={"https://github.com/regshield/regshield": "readme"},
    )
    assert result.items == [] and result.skipped_known == 1
    assert [s.repo for s in result.star_snapshots] == ["regshield/regshield"]


def test_github_search_paces_requests(web, make_client, clock):
    web.add(GH_API, json.dumps({"total_count": 0, "items": []}))
    GitHubSearchCollector(
        make_client(), gh_cfg(queries=["aa", "bb", "cc"], min_interval_s=6.5),
        now=lambda: NOW, clock=clock, sleep=clock.sleep,
    ).collect(src(FetchMethod.GITHUB_SEARCH, GH_API), since=SINCE, backfill=False, known={})  # fmt: skip
    assert clock.sleeps.count(6.5) == 2


def test_github_search_rate_limit_waits_once_then_fails(web, make_client, clock):
    web.routes[GH_API] = lambda req: httpx.Response(403)
    collector = GitHubSearchCollector(
        make_client(), gh_cfg(), now=lambda: NOW, clock=clock, sleep=clock.sleep
    )
    with pytest.raises(GitHubRateLimited):
        collector.collect(
            src(FetchMethod.GITHUB_SEARCH, GH_API), since=SINCE, backfill=False, known={}
        )
    assert 60.0 in clock.sleeps
    assert web.count(web.urls()[0]) == 2


def test_noise_filter_reasons():
    f = NoiseFilter(gh_cfg())
    assert f.reason({"name": "awesome-mcp-security"}) == "awesome_list"
    assert f.reason({"name": "mcp-guard", "description": "A tutorial for MCP"}) == "tutorial"
    assert f.reason({"name": "mcp-guard", "description": "Guards MCP servers"}) is None


def test_star_velocity_first_run_and_later():
    created = datetime(2026, 9, 20, tzinfo=UTC)
    first = velocity_from([(NOW, 300, created)], NOW)
    assert first.basis == "age" and first.per_day == pytest.approx(300 / 30.5, rel=1e-3)
    later = velocity_from([(NOW, 400, created), (NOW - timedelta(days=2), 300, created)], NOW)
    assert later.basis == "snapshots" and later.per_day == pytest.approx(50)
    assert velocity_from([], NOW) is None


# ---------- CNCF landscape ----------


CATS = ["Provisioning > Security & Compliance", "Provisioning > Container Registry"]


def test_cncf_parse_and_additions():
    before = parse(fixture("cncf_before.yml").decode())
    after = parse(fixture("cncf_after.yml").decode())
    added = additions(flatten(before), after, set(CATS))
    # Chainloop is new in a relevant category; "Moved Project" only moved; NewMetricsDB is new
    # but in a category we don't watch.
    assert [p.name for p in added] == ["Chainloop"]
    chainloop = added[0]
    assert chainloop.description == "Evidence store for your software supply chain attestations."
    assert chainloop.repo == "https://github.com/chainloop-dev/chainloop"
    assert chainloop.maturity == "sandbox"


def test_cncf_first_run_is_baseline_then_diff(web, make_client, collect_db):
    web.add(LANDSCAPE, fixture("cncf_before.yml"))
    source = _store_source(collect_db, src(FetchMethod.CNCF_LANDSCAPE, LANDSCAPE))
    cfg = CNCFSettings(categories=CATS)
    first = CNCFLandscapeCollector(make_client(), collect_db, cfg, now=lambda: NOW).collect(
        source, since=SINCE, backfill=False, known={}
    )
    assert first.items == [] and len(first.snapshots) == 1
    assert any("baseline" in n for n in first.notes)
    _store_snapshots(collect_db, source, first)

    web.add(LANDSCAPE, fixture("cncf_after.yml"))
    later = NOW + timedelta(days=1)
    second = CNCFLandscapeCollector(make_client(), collect_db, cfg, now=lambda: later).collect(
        source, since=SINCE, backfill=False, known={}
    )
    [item] = second.items
    assert item.title == "CNCF landscape adds Chainloop (Provisioning > Security & Compliance)"
    assert item.canonical_url == "https://landscape.cncf.io?item=chainloop"
    assert item.published_at == later
    assert item.meta["discovery"] == "cncf" and item.meta["cncf"]["repo_url"].endswith("chainloop")
    assert item.meta["after_snapshot_key"] == "cncf-live"

    # Same copy again: nothing new stored, nothing reported.
    _store_snapshots(collect_db, source, second)
    third = CNCFLandscapeCollector(make_client(), collect_db, cfg, now=lambda: later).collect(
        source, since=SINCE, backfill=False, known={}
    )
    assert third.items == [] and third.snapshots == []


def test_cncf_backfill_uses_commit_history(web, make_client, collect_db):
    source = _store_source(collect_db, src(FetchMethod.CNCF_LANDSCAPE, LANDSCAPE))
    cfg = CNCFSettings(categories=CATS, history_days=[90])
    commit_at = NOW - timedelta(days=95)
    web.add(
        cfg.commits_api,
        json.dumps([{"sha": "abc123", "commit": {"committer": {"date": commit_at.isoformat()}}}]),
    )
    web.add(cfg.raw_at_commit.format(sha="abc123"), fixture("cncf_before.yml"))
    web.add(LANDSCAPE, fixture("cncf_after.yml"))
    result = CNCFLandscapeCollector(make_client(), collect_db, cfg, now=lambda: NOW).collect(
        source, since=SINCE, backfill=True, known={}
    )
    assert [s.meta["origin"] for s in result.snapshots] == ["git", "live"]
    [item] = result.items
    assert item.meta["cncf"]["before_captured_at"] == commit_at.isoformat()
    assert "until=" in next(u for u in web.urls() if u.startswith(cfg.commits_api))


def _store_source(factory, source: SourceRef) -> SourceRef:
    from dataclasses import replace

    from app.db.models import Source

    with factory.begin() as s:
        row = Source(
            name=source.name, url=source.url, kind="community",
            fetch_method=source.fetch_method, trust_score=0.7,
        )  # fmt: skip
        s.add(row)
        s.flush()
        return replace(source, id=row.id)


def _store_snapshots(factory, source: SourceRef, result) -> None:
    from app.pipeline import snapshots

    with factory.begin() as s:
        for snap in result.snapshots:
            snapshots.insert(s, source.id, snap)
