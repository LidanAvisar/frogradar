"""The agent's web tools: Tavily search and a guarded page fetch (robots.txt, rate limit, SSRF
guard, size / time / redirect caps, text/html only, trafilatura main text)."""

import logging
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.agents.mission.safe_url import Resolver, UnsafeUrl, check_url, system_resolver
from app.core.catalog import MissionSettings
from app.core.config import Settings
from app.pipeline.collectors.http import FetchError, PoliteClient

logger = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class Page:
    url: str  # final URL after redirects
    title: str
    text: str


class SkipPage(FetchError):
    """Fetched but not usable: not HTML, no readable text (login wall, paywall, JS-only page)."""


SearchFn = Callable[[str, int, str | None], list[WebResult]]
FetchFn = Callable[[str], Page]


def time_range(days: int | None) -> str | None:
    if not days:
        return None
    if days <= 1:
        return "day"
    if days <= 7:
        return "week"
    if days <= 31:
        return "month"
    return "year"


def tavily_search(settings: Settings, *, transport: httpx.BaseTransport | None = None) -> SearchFn:
    def search(query: str, max_results: int, window: str | None) -> list[WebResult]:
        if not settings.tavily_api_key:
            raise FetchError("TAVILY_API_KEY is not set: web search skipped")
        body: dict[str, object] = {
            "query": query[:400],
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }
        if window:
            body["time_range"] = window
        try:
            with httpx.Client(timeout=20.0, transport=transport) as client:
                resp = client.post(
                    TAVILY_URL,
                    json=body,
                    headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
                )
        except httpx.HTTPError as e:
            raise FetchError(f"Tavily request failed: {type(e).__name__}") from e
        if not resp.is_success:
            raise FetchError(f"Tavily HTTP {resp.status_code}")
        out = []
        for r in (resp.json().get("results") or [])[:max_results]:
            url = str(r.get("url") or "")
            if url:
                out.append(
                    WebResult(
                        title=str(r.get("title") or url)[:300],
                        url=url,
                        snippet=str(r.get("content") or "")[:500],
                    )
                )
        return out

    return search


def _title(html: str) -> str:
    import lxml.html

    try:
        doc = lxml.html.fromstring(html)
    except Exception:  # noqa: BLE001 - title is cosmetic
        return ""
    t = doc.findtext(".//title") or ""
    return " ".join(t.split())[:300]


def extract_text(html: str) -> str:
    import trafilatura  # heavy import

    return (
        trafilatura.extract(html, favor_recall=True, include_tables=True, include_comments=False)
        or ""
    )


def page_fetcher(
    settings: Settings,
    cfg: MissionSettings,
    *,
    resolve: Resolver = system_resolver,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] | None = None,
) -> tuple[FetchFn, Callable[[], None]]:
    """(fetch, close). One PoliteClient per mission run: robots.txt cache and per-host delays."""
    kwargs: dict[str, object] = {}
    if sleep is not None:
        kwargs["sleep"] = sleep
    client = PoliteClient(
        settings,
        transport=transport,
        url_guard=lambda u: check_url(u, resolve),
        max_bytes=cfg.fetch_max_bytes,
        max_redirects=cfg.fetch_max_redirects,
        timeout_s=cfg.fetch_timeout_s,
        **kwargs,  # type: ignore[arg-type]
    )

    def fetch(url: str) -> Page:
        check_url(url, resolve)  # fail fast with the SSRF reason before robots.txt
        resp = client.get(url, headers={"Accept": "text/html,application/xhtml+xml"})
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype not in ("text/html", "application/xhtml+xml"):
            raise SkipPage(f"not an HTML page ({ctype or 'no content type'})")
        html = resp.text
        text = extract_text(html).strip()
        if len(text) < cfg.min_page_chars:
            raise SkipPage("no readable article text (login wall, paywall or JavaScript page)")
        return Page(url=str(resp.request.url), title=_title(html), text=text[: cfg.excerpt_chars])

    return fetch, client.close


__all__ = [
    "FetchError",
    "FetchFn",
    "Page",
    "SearchFn",
    "SkipPage",
    "UnsafeUrl",
    "WebResult",
    "page_fetcher",
    "tavily_search",
    "time_range",
]
