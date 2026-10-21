"""SSRF guard (D72) and the mission page fetcher: schemes, private addresses, redirects, limits."""

import httpx
import pytest

from app.agents.mission.safe_url import UnsafeUrl, check_url
from app.agents.mission.web import SkipPage, page_fetcher, tavily_search
from app.core.catalog import MissionSettings
from app.pipeline.collectors.http import FetchError
from tests.pipeline.conftest import FakeWeb, make_settings

PUBLIC = "93.184.216.34"


def resolver(table: dict[str, list[str]]):  # noqa: ANN201
    def resolve(host: str) -> list[str]:
        return table.get(host, [PUBLIC])

    return resolve


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://app.localhost/",
        "http://127.0.0.1/",
        "http://127.1.2.3:80/",
        "http://10.0.0.5/",
        "http://172.16.3.4/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fe80::1]/",
        "http://[fc00::1]/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://100.64.0.1/",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "gopher://example.com/",
        "javascript:alert(1)",
        "https://user:pw@example.com/",
        "https://example.com:8080/",
    ],
)
def test_unsafe_urls_are_blocked(url):
    with pytest.raises(UnsafeUrl):
        check_url(url, resolver({}))


def test_host_names_are_checked_by_every_resolved_address():
    table = {"internal.example.com": ["10.1.2.3"], "mixed.example.com": [PUBLIC, "127.0.0.1"],
             "meta.example.com": ["169.254.169.254"], "v6.example.com": ["::1"]}  # fmt: skip
    for host in table:
        with pytest.raises(UnsafeUrl):
            check_url(f"https://{host}/", resolver(table))
    check_url("https://www.sonatype.com/blog", resolver({}))  # public: fine


CFG = MissionSettings(min_page_chars=20, fetch_max_bytes=20_000, fetch_max_redirects=3)
ARTICLE = (
    "<html><head><title>Post</title></head><body><article><p>"
    + ("Sonatype adds reachability analysis. " * 10)
    + "</p></article></body></html>"
)


def _fetcher(web: FakeWeb, table: dict[str, list[str]] | None = None):  # noqa: ANN202
    return page_fetcher(
        make_settings(collector_domain_delay_s=0),
        CFG,
        resolve=resolver(table or {}),
        transport=httpx.MockTransport(web.handler),
        sleep=lambda _: None,
    )


def test_redirect_to_a_private_ip_is_blocked_before_the_request():
    web = FakeWeb()
    web.add("https://good.example.com/post", status=302, location="http://10.0.0.5/admin")
    fetch, close = _fetcher(web)
    with pytest.raises(UnsafeUrl):
        fetch("https://good.example.com/post")
    close()
    assert "http://10.0.0.5/admin" not in web.urls()
    assert "http://10.0.0.5/robots.txt" not in web.urls()


def test_redirect_to_a_host_resolving_to_metadata_ip_is_blocked():
    web = FakeWeb()
    web.add("https://good.example.com/post", status=301, location="https://evil.example.com/x")
    fetch, close = _fetcher(web, {"evil.example.com": ["169.254.169.254"]})
    with pytest.raises(UnsafeUrl):
        fetch("https://good.example.com/post")
    close()
    assert not any("evil.example.com" in u for u in web.urls())


def test_html_page_is_fetched_and_extracted():
    web = FakeWeb()
    web.add("https://good.example.com/post", ARTICLE, content_type="text/html; charset=utf-8")
    fetch, close = _fetcher(web)
    page = fetch("https://good.example.com/post")
    close()
    assert "reachability analysis" in page.text and page.title == "Post"


def test_non_html_and_oversized_and_short_pages_are_skipped():
    web = FakeWeb()
    web.add("https://good.example.com/file.pdf", b"%PDF-1.7", content_type="application/pdf")
    web.add("https://good.example.com/big", "<p>" + "x" * 30_000 + "</p>", content_type="text/html")
    web.add(
        "https://good.example.com/login",
        "<html><body>Sign in</body></html>",
        content_type="text/html",
    )
    fetch, close = _fetcher(web)
    with pytest.raises(SkipPage, match="not an HTML page"):
        fetch("https://good.example.com/file.pdf")
    with pytest.raises(FetchError, match="larger than"):
        fetch("https://good.example.com/big")
    with pytest.raises(SkipPage, match="no readable article text"):
        fetch("https://good.example.com/login")
    close()


def test_too_many_redirects():
    web = FakeWeb()
    for i in range(5):
        web.add(
            f"https://good.example.com/r{i}",
            status=302,
            location=f"https://good.example.com/r{i + 1}",
        )
    fetch, close = _fetcher(web)
    with pytest.raises(FetchError, match="too many redirects"):
        fetch("https://good.example.com/r0")
    close()


def test_robots_disallow_is_respected():
    web = FakeWeb()
    web.add(
        "https://good.example.com/robots.txt",
        "User-agent: *\nDisallow: /private",
        content_type="text/plain",
    )
    web.add("https://good.example.com/private/post", ARTICLE, content_type="text/html")
    fetch, close = _fetcher(web)
    with pytest.raises(FetchError, match="robots.txt disallows"):
        fetch("https://good.example.com/private/post")
    close()
    assert "https://good.example.com/private/post" not in web.urls()


def test_tavily_search_shape_and_missing_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"results": [
            {"title": "A", "url": "https://a.example.com/x", "content": "snippet"},
            {"title": "no url"},
        ]})  # fmt: skip

    search = tavily_search(
        make_settings(tavily_api_key="tvly-test"), transport=httpx.MockTransport(handler)
    )
    out = search("sonatype reachability", 4, "month")
    assert [r.url for r in out] == ["https://a.example.com/x"]
    assert seen["auth"] == "Bearer tvly-test" and '"time_range":"month"' in seen["body"].replace(
        " ", ""
    )
    with pytest.raises(FetchError, match="TAVILY_API_KEY"):
        tavily_search(make_settings(tavily_api_key=""))("q", 2, None)
