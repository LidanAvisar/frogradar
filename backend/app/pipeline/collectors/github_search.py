"""GitHub search discovery collector: repositories created recently that match our topics.

    GET /search/repositories?q=<query> created:>YYYY-MM-DD stars:>=N&sort=stars&order=desc

- Unauthenticated the search API allows 10 requests/minute: requests are spaced by
  github.min_interval_s; a 403/429 waits once (60 s) and retries, then the source fails.
  GITHUB_TOKEN (optional) raises the limit.
- Noise filter in code, before anything is stored: forks, archived repos and names /
  descriptions that mark lists, tutorials, demos, examples, courses, CTFs or homework (patterns
  in discovery.yaml). Skips are counted per reason (CollectResult.skipped).
- One signal per repo (canonical = repo URL); text = name, description, topics, homepage,
  language, license, created date and a README excerpt (raw.githubusercontent.com, no quota).
- Stars are NOT in the text: every run records a star snapshot per repo (repo_star_snapshots),
  including repos already stored, so star velocity can be computed across runs.
"""

import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from app.core.catalog import GitHubSearchSettings
from app.pipeline.collectors.base import CollectResult, KnownUrls, RawItem, SourceRef
from app.pipeline.collectors.discovery import KIND, fetch_readme
from app.pipeline.collectors.github import GitHubRateLimited
from app.pipeline.collectors.http import FetchError, PoliteClient
from app.pipeline.normalize import canonical_url, to_utc
from app.pipeline.radar.stars import StarSnapshot

logger = logging.getLogger(__name__)

RATE_LIMIT_WAIT_S = 60.0


class NoiseFilter:
    """Why a repo is skipped (None = keep). Case-insensitive patterns from discovery.yaml."""

    def __init__(self, cfg: GitHubSearchSettings) -> None:
        self.cfg = cfg
        self.name = {k: re.compile(v, re.IGNORECASE) for k, v in cfg.skip_name_patterns.items()}
        self.desc = {
            k: re.compile(v, re.IGNORECASE) for k, v in cfg.skip_description_patterns.items()
        }

    def reason(self, repo: dict[str, Any]) -> str | None:
        if self.cfg.skip_forks and repo.get("fork"):
            return "fork"
        if self.cfg.skip_archived and repo.get("archived"):
            return "archived"
        name = str(repo.get("name") or "")
        for reason, pattern in self.name.items():
            if pattern.search(name):
                return reason
        description = str(repo.get("description") or "")
        for reason, pattern in self.desc.items():
            if pattern.search(description):
                return reason
        return None


class GitHubSearchCollector:
    def __init__(
        self,
        client: PoliteClient,
        cfg: GitHubSearchSettings,
        *,
        token: str = "",
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.cfg = cfg
        self.now = now
        self.clock = clock
        self.sleep = sleep
        self.noise = NoiseFilter(cfg)
        self._last: float | None = None
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
        created_after = (self.now() - timedelta(days=self.cfg.created_within_days)).date()
        repos: dict[str, dict[str, Any]] = {}
        for query in self.cfg.queries:
            for repo in self._search(source.url, query, created_after):
                full = str(repo.get("full_name") or "").lower()
                if not full:
                    continue
                entry = repos.setdefault(full, {**repo, "_matched": []})
                entry["_matched"].append(query)

        captured = self.now()
        for full, repo in sorted(repos.items()):
            if (reason := self.noise.reason(repo)) is not None:
                result.skip(reason)
                continue
            result.star_snapshots.append(
                StarSnapshot(
                    repo=full,
                    captured_at=captured,
                    stars=int(repo.get("stargazers_count") or 0),
                    forks=repo.get("forks_count"),
                    repo_created_at=to_utc(repo.get("created_at")),
                )
            )
            item = self._item(repo)
            if item.canonical_url in known:
                result.skipped_known += 1
                continue
            owner, name = full.split("/", 1)
            readme = fetch_readme(self.client, owner, name, self.cfg.readme_chars)
            if readme:
                item.text += f"\n\nREADME (excerpt):\n{readme}"
                item.meta["text_source"] = "readme"
            result.items.append(item)
        result.notes.append(f"{len(repos)} distinct repos from {len(self.cfg.queries)} searches")
        return result

    def _search(self, base: str, query: str, created_after: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        q = f"{query} created:>{created_after.isoformat()} stars:>={self.cfg.min_stars}"
        for page in range(1, self.cfg.max_pages + 1):
            params = {
                "q": q,
                "sort": "stars",
                "order": "desc",
                "per_page": str(self.cfg.per_page),
                "page": str(page),
            }
            data = self._get(f"{base}?{urlencode(params)}").json()
            items = data.get("items") or []
            out.extend(items)
            if len(items) < self.cfg.per_page or page * self.cfg.per_page >= int(
                data.get("total_count") or 0
            ):
                break
        return out

    def _get(self, url: str) -> Any:
        for attempt in (1, 2):
            self._pace()
            try:
                return self.client.get(url, headers=self.headers)
            except FetchError as e:
                limited = "HTTP 403" in str(e) or "HTTP 429" in str(e)
                if not limited:
                    raise
                if attempt == 2:
                    raise GitHubRateLimited(
                        f"{e} (GitHub search rate limit; set GITHUB_TOKEN for more)"
                    ) from e
                logger.warning("GitHub search rate limited, waiting %.0fs", RATE_LIMIT_WAIT_S)
                self.sleep(RATE_LIMIT_WAIT_S)
        raise AssertionError("unreachable")

    def _pace(self) -> None:
        if self._last is not None:
            remaining = self.cfg.min_interval_s - (self.clock() - self._last)
            if remaining > 0:
                self.sleep(remaining)
        self._last = self.clock()

    @staticmethod
    def _item(repo: dict[str, Any]) -> RawItem:
        full = str(repo["full_name"])
        url = str(repo.get("html_url") or f"https://github.com/{full}")
        description = (repo.get("description") or "").strip()
        topics = [str(t) for t in repo.get("topics") or []]
        homepage = (repo.get("homepage") or "").strip() or None
        license_ = (repo.get("license") or {}).get("spdx_id")
        created = to_utc(repo.get("created_at"))
        lines = [
            f"New GitHub repository {full}" + (f": {description}" if description else ""),
            f"Repository: {url}",
            f"Homepage: {homepage}" if homepage else "",
            f"Topics: {', '.join(topics)}" if topics else "",
            f"Language: {repo['language']}" if repo.get("language") else "",
            f"License: {license_}" if license_ and license_ != "NOASSERTION" else "",
            f"Created: {created.date().isoformat()}" if created else "",
        ]
        return RawItem(
            url=url,
            canonical_url=canonical_url(url.lower()),
            title=f"New GitHub repository: {full}"
            + (f" – {description[:120]}" if description else ""),
            text="\n".join(line for line in lines if line),
            published_at=created,
            meta={
                "kind": KIND,
                "discovery": "github",
                "text_source": "api",
                "repo": {
                    "full_name": full.lower(),
                    "name": repo.get("name"),
                    "owner": (repo.get("owner") or {}).get("login"),
                    "owner_type": (repo.get("owner") or {}).get("type"),
                    "html_url": url,
                    "homepage": homepage,
                    "topics": topics,
                    "language": repo.get("language"),
                    "created_at": created.isoformat() if created else None,
                    "stars_at_first_seen": int(repo.get("stargazers_count") or 0),
                    "matched": sorted(set(repo.get("_matched") or [])),
                },
            },
        )
