"""GitHub releases collector (REST API). Repos come from source.options.repos.

Normal run: the newest page per repo. Backfill: page back until --since.
Drafts are skipped (only visible with push access anyway); pre-releases are kept and flagged.
"""

import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.pipeline.collectors.base import CollectResult, KnownUrls, RawItem, SourceRef
from app.pipeline.collectors.http import FetchError, PoliteClient
from app.pipeline.normalize import canonical_url, to_utc

logger = logging.getLogger(__name__)

API = "https://api.github.com"
PER_PAGE_NORMAL = 30
PER_PAGE_BACKFILL = 100
MAX_PAGES = 20  # safety net: 2000 releases per repo


class GitHubRateLimited(FetchError):
    pass


class GitHubCollector:
    def __init__(self, client: PoliteClient, token: str = "") -> None:
        self.client = client
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def collect(
        self, source: SourceRef, *, since: datetime, backfill: bool, known: KnownUrls
    ) -> CollectResult:
        result = CollectResult()
        for repo in source.options["repos"]:
            full = repo if "/" in repo else f"{_owner(source.url)}/{repo}"
            self._collect_repo(full, result, since=since, backfill=backfill)
        return result

    def _collect_repo(
        self, repo: str, result: CollectResult, *, since: datetime, backfill: bool
    ) -> None:
        per_page = PER_PAGE_BACKFILL if backfill else PER_PAGE_NORMAL
        url: str | None = f"{API}/repos/{repo}/releases?per_page={per_page}"
        pages = 0
        while url and pages < MAX_PAGES:
            resp = self._get(url)
            pages += 1
            releases = resp.json()
            page_all_old = bool(releases)
            for rel in releases:
                if rel.get("draft"):
                    continue
                published = to_utc(rel.get("published_at") or rel.get("created_at"))
                if result.see(published, since):
                    page_all_old = False
                    result.items.append(_to_item(repo, rel, published))
            if not backfill or page_all_old:
                break
            url = _next_link(resp)
        if pages >= MAX_PAGES and url:
            result.notes.append(f"{repo}: stopped after {MAX_PAGES} pages")

    def _get(self, url: str) -> httpx.Response:
        try:
            return self.client.get(url, headers=self.headers)
        except FetchError as e:
            if "HTTP 403" in str(e) or "HTTP 429" in str(e):
                raise GitHubRateLimited(
                    f"{e} (GitHub rate limit? set GITHUB_TOKEN for 5000 requests/hour)"
                ) from e
            raise


def _owner(org_url: str) -> str:
    return urlsplit(org_url).path.strip("/").split("/")[0]


def _next_link(resp: httpx.Response) -> str | None:
    return resp.links.get("next", {}).get("url")


def _to_item(repo: str, rel: dict[str, Any], published: datetime | None) -> RawItem:
    tag = rel.get("tag_name") or ""
    name = (rel.get("name") or "").strip()
    title = f"{repo} {tag}" + (f" – {name}" if name and name != tag else "")
    body = (rel.get("body") or "").strip()
    url = rel.get("html_url") or f"https://github.com/{repo}/releases/tag/{tag}"
    return RawItem(
        url=url,
        canonical_url=canonical_url(url),
        title=title,
        text=body or f"Release {tag} of {repo} (no release notes).",
        published_at=published,
        meta={
            "text_source": "api",
            "repo": repo,
            "tag": tag,
            "prerelease": bool(rel.get("prerelease")),
            "author": (rel.get("author") or {}).get("login"),
        },
    )
