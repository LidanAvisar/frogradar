"""One polite HTTP client for every collector (httpx).

- identifying User-Agent, timeouts
- robots.txt checked per host (cached for the client's lifetime, i.e. one run). Per RFC 9309:
  robots.txt 4xx → everything allowed; 5xx / unreachable → nothing allowed on that host.
  Documented APIs we call as an API client (api.github.com) are exempt.
- minimum delay between requests to the same host (robots.txt Crawl-delay or a per-host
  override wins if larger)
- redirects followed by hand, so every hop is checked against robots.txt and rate-limited
- conditional GET via If-None-Match / If-Modified-Since
"""

import logging
import time
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

ROBOTS_EXEMPT_HOSTS = frozenset({"api.github.com"})
MAX_REDIRECTS = 5
MAX_CRAWL_DELAY_S = 30.0
_ROBOTS_TOKEN = "FrogRadarBot"


class FetchError(Exception):
    """A request failed (network, HTTP status, robots.txt)."""


class RobotsDisallowed(FetchError):
    pass


class _Robots:
    def __init__(self, parser: RobotFileParser | None, allow_all: bool) -> None:
        self.parser = parser
        self.allow_all = allow_all

    def allowed(self, url: str) -> bool:
        if self.allow_all:
            return True
        return self.parser is not None and self.parser.can_fetch(_ROBOTS_TOKEN, url)

    def crawl_delay(self) -> float | None:
        if self.parser is None:
            return None
        delay = self.parser.crawl_delay(_ROBOTS_TOKEN)
        return float(delay) if delay is not None else None


class PoliteClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        host_delays: dict[str, float] | None = None,
        url_guard: Callable[[str], None] | None = None,
        max_bytes: int | None = None,
        max_redirects: int = MAX_REDIRECTS,
        timeout_s: float | None = None,
    ) -> None:
        self.settings = settings
        # Research missions (D72): url_guard raises FetchError for unsafe URLs; it runs before every
        # request (robots.txt and each redirect hop included). max_bytes caps the response body.
        self.url_guard = url_guard
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        # Slower minimum gaps for specific hosts (e.g. web.archive.org); the larger delay wins.
        self.host_delays = {h.lower(): d for h, d in (host_delays or {}).items()}
        self.user_agent = settings.collector_user_agent
        self._client = httpx.Client(
            headers={"User-Agent": self.user_agent},
            timeout=httpx.Timeout(timeout_s or settings.collector_timeout_s),
            follow_redirects=False,
            transport=transport,
        )
        self._clock = clock
        self._sleep = sleep
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, _Robots] = {}
        self.request_count = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------- public ----------

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> httpx.Response:
        """GET with robots.txt, rate limit and redirects. Returns 2xx/304, else FetchError."""
        req_headers = dict(headers or {})
        if etag:
            req_headers["If-None-Match"] = etag
        if last_modified:
            req_headers["If-Modified-Since"] = last_modified

        for _ in range(self.max_redirects + 1):
            if self.url_guard is not None:
                self.url_guard(url)  # every hop, before robots.txt (its own request is guarded too)
            host = urlsplit(url).netloc.lower()
            if host not in ROBOTS_EXEMPT_HOSTS:
                robots = self._robots_for(url)
                if not robots.allowed(url):
                    if robots.parser is None:  # 5xx / unreachable: RFC 9309 says stay away
                        raise RobotsDisallowed(f"robots.txt unavailable for {host}: not fetching")
                    raise RobotsDisallowed(f"robots.txt disallows {url}")
            resp = self._send(url, req_headers)
            if resp.is_redirect and "location" in resp.headers:
                url = urljoin(url, resp.headers["location"])
                continue
            if resp.status_code == 304 or resp.is_success:
                return resp
            raise FetchError(f"HTTP {resp.status_code} for {url}")
        raise FetchError(f"too many redirects for {url}")

    # ---------- internals ----------

    def _send(self, url: str, headers: dict[str, str]) -> httpx.Response:
        if self.url_guard is not None:
            self.url_guard(url)
        host = urlsplit(url).netloc.lower()
        self._wait(host)
        self.request_count += 1
        try:
            if self.max_bytes is None:
                return self._client.get(url, headers=headers)
            return self._capped_get(url, headers, self.max_bytes)
        except httpx.HTTPError as e:
            raise FetchError(f"{type(e).__name__} for {url}") from e
        finally:
            self._last_request[host] = self._clock()

    def _capped_get(self, url: str, headers: dict[str, str], limit: int) -> httpx.Response:
        request = self._client.build_request("GET", url, headers=headers)
        resp = self._client.send(request, stream=True)
        try:
            declared = resp.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > limit:
                raise FetchError(f"response larger than {limit} bytes for {url}")
            body = bytearray()
            for chunk in resp.iter_bytes():
                body += chunk
                if len(body) > limit:
                    raise FetchError(f"response larger than {limit} bytes for {url}")
        finally:
            resp.close()
        # iter_bytes() already decoded the body: drop the encoding headers of the original.
        keep = {
            k: v
            for k, v in resp.headers.items()
            if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
        }
        return httpx.Response(resp.status_code, headers=keep, content=bytes(body), request=request)

    def _delay_for(self, host: str) -> float:
        delay = max(self.settings.collector_domain_delay_s, self.host_delays.get(host, 0.0))
        robots = self._robots.get(host)
        crawl_delay = robots.crawl_delay() if robots else None
        if crawl_delay:
            delay = max(delay, min(crawl_delay, MAX_CRAWL_DELAY_S))
        return delay

    def _wait(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is None:
            return
        remaining = self._delay_for(host) - (self._clock() - last)
        if remaining > 0:
            self._sleep(remaining)

    def _robots_for(self, url: str) -> _Robots:
        parts = urlsplit(url)
        host = parts.netloc.lower()
        if host in self._robots:
            return self._robots[host]
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        try:
            resp = self._send(robots_url, {})
            # One redirect hop is common (http→https, apex→www); follow it without a robots check.
            if resp.is_redirect and "location" in resp.headers:
                resp = self._send(urljoin(robots_url, resp.headers["location"]), {})
        except FetchError as e:
            logger.warning("robots.txt unreachable for %s (%s): not fetching from it", host, e)
            robots = _Robots(None, allow_all=False)
        else:
            if resp.is_success:
                parser = RobotFileParser()
                parser.parse(resp.text.splitlines())
                robots = _Robots(parser, allow_all=False)
            elif 400 <= resp.status_code < 500:
                robots = _Robots(None, allow_all=True)
            else:
                logger.warning("robots.txt for %s: HTTP %s, not fetching", host, resp.status_code)
                robots = _Robots(None, allow_all=False)
        self._robots[host] = robots
        return robots
