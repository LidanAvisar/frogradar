import json
from datetime import UTC, datetime

import httpx
import pytest

from app.db.enums import FetchMethod
from app.pipeline.collectors import NotImplementedCollector, SourceRef
from app.pipeline.collectors.github import GitHubCollector, GitHubRateLimited
from app.pipeline.collectors.rss import RSSCollector
from app.pipeline.collectors.stubs import WebCollector
from tests.pipeline.conftest import fixture

SINCE = datetime(2026, 7, 22, tzinfo=UTC)  # "90 days" before 2026-10-20
FEED = "https://blog.acme.test/feed"
ARTICLE = "https://blog.acme.test/firewall-2/?utm_source=rss&utm_medium=feed"


def rss_source(**kw) -> SourceRef:
    return SourceRef(
        id=1, name="Acme Blog", url=FEED, fetch_method=FetchMethod.RSS, options={}, **kw
    )


def gh_source(repos=("acme-cli",)) -> SourceRef:
    return SourceRef(
        id=2, name="Acme GitHub", url="https://github.com/acme", fetch_method=FetchMethod.GITHUB,
        options={"repos": list(repos)},
    )  # fmt: skip


def collect_rss(client, source=None, *, since=SINCE, backfill=False, known=None):
    return RSSCollector(client).collect(
        source or rss_source(), since=since, backfill=backfill, known=known or {}
    )


def collect_gh(client, source=None, *, since=SINCE, backfill=False, token=""):
    return GitHubCollector(client, token=token).collect(
        source or gh_source(), since=since, backfill=backfill, known={}
    )


# ---------- RSS ----------


def test_rss_parses_items_and_fetches_short_articles(web, make_client):
    web.add(
        FEED, fixture("blog_rss.xml"), etag='"v1"', last_modified="Tue, 20 Oct 2026 08:00:00 GMT"
    )
    web.add(ARTICLE, fixture("article_firewall.html"))

    result = RSSCollector(make_client()).collect(
        rss_source(), since=SINCE, backfill=False, known={}
    )

    assert [i.title for i in result.items] == [
        "Acme launches Package Firewall 2.0",
        "Deep dive: SBOM export in Acme Registry",
    ]
    firewall, sbom = result.items
    assert firewall.canonical_url == "https://blog.acme.test/firewall-2"
    assert firewall.published_at == datetime(2026, 10, 19, 12, 0, tzinfo=UTC)  # +0200 → UTC
    assert firewall.meta["text_source"] == "article"
    assert "behavioural analysis of install scripts" in firewall.text
    assert "All rights reserved" not in firewall.text and "Login" not in firewall.text
    assert sbom.meta["text_source"] == "feed"  # long enough: no article fetch
    assert "CycloneDX" in sbom.text
    assert web.count("https://blog.acme.test/sbom-export") == 0
    # History: the feed's oldest item is before --since, so the window is fully covered.
    assert result.too_old == 1 and result.reached_since
    assert result.oldest_seen == datetime(2025, 7, 1, 10, 0, tzinfo=UTC)
    assert (result.etag, result.last_modified) == ('"v1"', "Tue, 20 Oct 2026 08:00:00 GMT")


def test_rss_atom_feed(web, make_client):
    web.add(FEED, fixture("changelog_atom.xml"))
    result = RSSCollector(make_client()).collect(
        rss_source(), since=SINCE, backfill=False, known={}
    )
    [item] = result.items
    assert item.canonical_url == "https://changelog.acme.test/2026/retention"
    assert item.published_at == datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    assert not result.reached_since  # feed doesn't go back to --since
    assert result.oldest_seen == item.published_at


def test_rss_skips_article_fetch_for_known_urls(web, make_client):
    web.add(FEED, fixture("blog_rss.xml"))
    known = {"https://blog.acme.test/firewall-2": "article"}
    result = RSSCollector(make_client()).collect(
        rss_source(), since=SINCE, backfill=False, known=known
    )
    assert result.skipped_known == 1 and len(result.items) == 1
    assert web.count(ARTICLE) == 0


def test_rss_keeps_feed_text_when_extraction_fails(web, make_client):
    web.add(FEED, fixture("blog_rss.xml"))
    web.add(ARTICLE, "", status=500)
    result = RSSCollector(make_client()).collect(
        rss_source(), since=SINCE, backfill=False, known={}
    )
    firewall = result.items[0]
    assert firewall.meta["text_source"] == "feed"
    assert firewall.text == "Short teaser about the new firewall."
    assert result.extract_failed == 1


def test_rss_not_modified(web, make_client):
    web.add(FEED, "", status=304)
    src = rss_source(http_etag='"v1"', http_last_modified="Tue, 20 Oct 2026 08:00:00 GMT")
    result = collect_rss(make_client(), src)
    assert result.not_modified and result.items == []
    assert result.etag == '"v1"'


def test_rss_backfill_ignores_validators(web, make_client):
    web.add(FEED, fixture("changelog_atom.xml"))
    src = rss_source(http_etag='"v1"')
    collect_rss(make_client(), src, backfill=True)
    assert "if-none-match" not in web.requests[-1].headers


ANCHOR_FEED = (
    b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Release notes</title>
<entry><title>September 15, 2026</title><updated>2026-09-15T00:00:00-07:00</updated>
  <link href="https://docs.acme.test/release-notes#September_15_2026"/><id>a</id>
  <content type="html">Feature A is GA. """
    + b"x" * 600
    + b"""</content></entry>
<entry><title>July 24, 2026</title><updated>2026-07-24T00:00:00-07:00</updated>
  <link href="https://docs.acme.test/release-notes#July_24_2026"/><id>b</id>
  <content type="html">Feature B is in preview. """
    + b"y" * 600
    + b"""</content></entry>
</feed>"""
)


def test_rss_anchor_items_collide_without_keep_fragment(web, make_client):
    web.add(FEED, ANCHOR_FEED)
    result = collect_rss(make_client())
    [item] = result.items  # the second entry must not overwrite the first
    assert item.title == "September 15, 2026"
    assert result.notes and "keep_fragment" in result.notes[0]


def test_rss_keep_fragment_keeps_anchor_items_apart(web, make_client):
    web.add(FEED, ANCHOR_FEED)
    src = SourceRef(
        id=1, name="Notes", url=FEED, fetch_method=FetchMethod.RSS,
        options={"keep_fragment": True},
    )  # fmt: skip
    result = collect_rss(make_client(), src)
    assert [i.canonical_url for i in result.items] == [
        "https://docs.acme.test/release-notes#September_15_2026",
        "https://docs.acme.test/release-notes#July_24_2026",
    ]
    assert result.notes == []


def test_rss_rejects_non_feed(web, make_client):
    web.add(FEED, "<html><body>Not a feed <<<</body></html")
    with pytest.raises(Exception, match="not a feed"):
        collect_rss(make_client())


# ---------- GitHub ----------

RELEASES = "https://api.github.com/repos/acme/acme-cli/releases"


def _paged(web, per_page):
    pages = {1: "github_releases_page1.json", 2: "github_releases_page2.json",
             3: "github_releases_page3.json"}  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        headers = {}
        if page < 3:
            headers["link"] = f'<{RELEASES}?per_page={per_page}&page={page + 1}>; rel="next"'
        return httpx.Response(200, content=fixture(pages[page]), headers=headers)

    web.routes[RELEASES] = handler


def test_github_normal_run_reads_first_page_only(web, make_client):
    _paged(web, 30)
    result = GitHubCollector(make_client()).collect(
        gh_source(), since=SINCE, backfill=False, known={}
    )
    assert len(web.requests) == 1 and "per_page=30" in web.urls()[0]
    tags = [i.meta["tag"] for i in result.items]
    assert tags == ["v3.0.0-rc.1", "v2.9.0", "v2.8.0"]  # draft skipped
    rc, v29, v28 = result.items
    assert rc.meta["prerelease"] is True and v29.meta["prerelease"] is False
    assert v29.title == "acme/acme-cli v2.9.0 – Acme CLI 2.9 – SBOM support"
    assert v29.canonical_url == "https://github.com/acme/acme-cli/releases/tag/v2.9.0"
    assert v28.text == "Release v2.8.0 of acme/acme-cli (no release notes)."
    assert rc.published_at == datetime(2026, 10, 18, 10, 0, tzinfo=UTC)


def test_github_backfill_pages_until_since(web, make_client):
    _paged(web, 100)
    result = GitHubCollector(make_client()).collect(
        gh_source(), since=SINCE, backfill=True, known={}
    )
    # page 2 has v2.7.0 (in window) and v2.6.0 (too old) → page 3 is all old → stop after it.
    assert len(web.requests) == 3
    assert [i.meta["tag"] for i in result.items] == ["v3.0.0-rc.1", "v2.9.0", "v2.8.0", "v2.7.0"]
    assert result.reached_since and result.too_old == 3
    assert result.oldest_seen == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


def test_github_backfill_stops_on_a_page_with_only_old_releases(web, make_client):
    _paged(web, 100)
    since = datetime(2026, 9, 15, tzinfo=UTC)
    collect_gh(make_client(), since=since, backfill=True)
    assert len(web.requests) == 2  # page 2 is entirely older than since


def test_github_token_and_rate_limit(web, make_client):
    web.add(RELEASES, json.dumps({"message": "API rate limit exceeded"}), status=403)
    collector = GitHubCollector(make_client(), token="ghp_test")
    with pytest.raises(GitHubRateLimited, match="GITHUB_TOKEN"):
        collector.collect(gh_source(), since=SINCE, backfill=False, known={})
    assert web.requests[0].headers["authorization"] == "Bearer ghp_test"
    assert web.requests[0].headers["accept"] == "application/vnd.github+json"


def test_github_owner_from_org_url_or_explicit_repo(web, make_client):
    web.add(RELEASES, "[]")
    web.add("https://api.github.com/repos/other/tool/releases", "[]")
    GitHubCollector(make_client()).collect(
        gh_source(repos=("acme-cli", "other/tool")), since=SINCE, backfill=False, known={}
    )
    assert [u.split("?")[0] for u in web.urls()] == [
        RELEASES, "https://api.github.com/repos/other/tool/releases",
    ]  # fmt: skip


# ---------- stubs ----------


def test_web_is_a_stub():
    src = SourceRef(id=3, name="p", url="https://x.test", fetch_method=FetchMethod.WEB, options={})
    with pytest.raises(NotImplementedCollector):
        WebCollector().collect(src, since=SINCE, backfill=False, known={})
