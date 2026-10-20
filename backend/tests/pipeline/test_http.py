import httpx
import pytest

from app.pipeline.collectors import FetchError, RobotsDisallowed

ROBOTS = "https://blog.acme.test/robots.txt"


def test_user_agent_and_timeout(web, make_client):
    web.add("https://blog.acme.test/a", "ok")
    make_client().get("https://blog.acme.test/a")
    assert web.requests[-1].headers["user-agent"].startswith("FrogRadarBot/")


def test_robots_disallow_blocks_without_requesting_the_page(web, make_client):
    web.add(ROBOTS, "User-agent: *\nDisallow: /private\n")
    web.add("https://blog.acme.test/private/x", "secret")
    client = make_client()
    with pytest.raises(RobotsDisallowed):
        client.get("https://blog.acme.test/private/x")
    with pytest.raises(FetchError, match="404"):  # allowed, just not found
        client.get("https://blog.acme.test/public")
    assert "https://blog.acme.test/private/x" not in web.urls()
    assert web.count(ROBOTS) == 1  # cached per host


def test_robots_specific_agent_rules(web, make_client):
    web.add(ROBOTS, "User-agent: FrogRadarBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    with pytest.raises(RobotsDisallowed):
        make_client().get("https://blog.acme.test/a")


def test_robots_404_allows_and_5xx_blocks(web, make_client):
    web.add("https://ok.test/a", "fine")
    web.add("https://down.test/robots.txt", "", status=503)
    client = make_client()
    assert client.get("https://ok.test/a").text == "fine"  # robots.txt 404 → allow all
    with pytest.raises(RobotsDisallowed):
        client.get("https://down.test/a")


def test_robots_not_fetched_for_github_api(web, make_client):
    web.add("https://api.github.com/repos/a/b/releases", "[]")
    make_client().get("https://api.github.com/repos/a/b/releases")
    assert web.urls() == ["https://api.github.com/repos/a/b/releases"]


def test_per_host_rate_limit_and_crawl_delay(web, make_client, clock):
    web.add(ROBOTS, "User-agent: *\nCrawl-delay: 3\n")
    web.add("https://blog.acme.test/a", "a")
    web.add("https://other.test/a", "a")
    client = make_client(collector_domain_delay_s=1.0)
    client.get("https://blog.acme.test/a")
    client.get("https://other.test/a")  # different host: no wait for blog.acme.test
    client.get("https://blog.acme.test/a")
    assert clock.sleeps and max(clock.sleeps) == pytest.approx(3.0)  # Crawl-delay wins


def test_redirects_are_followed_with_robots_check(web, make_client):
    web.routes["https://blog.acme.test/old"] = lambda r: httpx.Response(
        301, headers={"location": "https://www.acme.test/new"}
    )
    web.add("https://www.acme.test/robots.txt", "User-agent: *\nDisallow: /new\n")
    with pytest.raises(RobotsDisallowed):
        make_client().get("https://blog.acme.test/old")


def test_conditional_get_headers_and_304(web, make_client):
    web.add("https://blog.acme.test/feed", "", status=304)
    resp = make_client().get(
        "https://blog.acme.test/feed", etag='"abc"', last_modified="Mon, 19 Oct 2026 10:00:00 GMT"
    )
    assert resp.status_code == 304
    sent = web.requests[-1].headers
    assert sent["if-none-match"] == '"abc"'
    assert sent["if-modified-since"] == "Mon, 19 Oct 2026 10:00:00 GMT"


def test_http_errors_raise(web, make_client):
    web.add("https://blog.acme.test/boom", "", status=500)
    with pytest.raises(FetchError, match="HTTP 500"):
        make_client().get("https://blog.acme.test/boom")


def test_network_errors_raise_fetch_error(make_client, web):
    def broken(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    web.routes["https://slow.test/robots.txt"] = lambda r: httpx.Response(404)
    web.routes["https://slow.test/a"] = broken
    with pytest.raises(FetchError, match="ConnectTimeout"):
        make_client().get("https://slow.test/a")
