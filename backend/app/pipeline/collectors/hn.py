"""Hacker News discovery collector (Algolia HN Search API, no key).

Two kinds of searches, queries from config/discovery.yaml (hn):
- keyword stories: tags=story, the query must match the TITLE (restrictSearchableAttributes),
  points >= keywords.min_points;
- Show HN posts: tags=show_hn, the query may match title or text, points >= show_hn.min_points.
Typo tolerance is off and phrases in quotes are exact (advancedSyntax): with typos on, "SBOM"
matched 286 unrelated stories in 90 days.

One signal per HN story: url = the linked page (the HN discussion if there is none), canonical =
the HN item URL (stable), text = title + post text + the linked page's text (trafilatura, robots
respected) or, for github.com links, the README. Points and comments go to meta.hn only.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from app.core.catalog import HNSettings
from app.pipeline.collectors.base import CollectResult, KnownUrls, RawItem, SourceRef
from app.pipeline.collectors.discovery import KIND, fetch_readme, github_repo
from app.pipeline.collectors.http import FetchError, PoliteClient
from app.pipeline.normalize import canonical_url, html_to_text

logger = logging.getLogger(__name__)

HN_ITEM = "https://news.ycombinator.com/item?id={id}"


def _extract(html: str, url: str) -> str | None:
    import trafilatura  # heavy import; only needed when a page is fetched

    return trafilatura.extract(html, url=url, include_comments=False, favor_precision=True)


class HNSearchCollector:
    def __init__(
        self,
        client: PoliteClient,
        cfg: HNSettings,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        extract: Callable[[str, str], str | None] = _extract,
    ) -> None:
        self.client = client
        self.cfg = cfg
        self.now = now
        self.extract = extract

    def searches(self) -> list[dict[str, str]]:
        """(tags, query, restrict, min_points) per search, in a stable order."""
        out = []
        for q in self.cfg.keywords.queries:
            out.append(
                {
                    "tags": "story",
                    "query": q,
                    "restrict": "title",
                    "min": self.cfg.keywords.min_points,
                }
            )
        for q in self.cfg.keywords.queries:
            out.append(
                {"tags": "show_hn", "query": q, "restrict": "", "min": self.cfg.show_hn.min_points}
            )
        return out

    def collect(
        self, source: SourceRef, *, since: datetime, backfill: bool, known: KnownUrls
    ) -> CollectResult:
        result = CollectResult()
        start = max(since, self.now() - timedelta(days=self.cfg.window_days))
        hits: dict[str, dict[str, Any]] = {}
        for search in self.searches():
            for hit in self._search(source.url, search, start):
                oid = str(hit.get("objectID") or "")
                if not oid:
                    continue
                entry = hits.setdefault(oid, {**hit, "_matched": []})
                entry["_matched"].append(f"{search['tags']}:{search['query']}")

        for oid, hit in sorted(hits.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
            published = _ts(hit.get("created_at_i"))
            if not result.see(published, start):
                continue
            item = self._item(oid, hit, published)
            if item.canonical_url in known:
                result.skipped_known += 1
                continue
            self._add_linked_text(item, result)
            result.items.append(item)
        result.notes.append(f"{len(hits)} distinct stories from {len(self.searches())} searches")
        return result

    def _search(self, base: str, search: dict[str, Any], start: datetime) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for page in range(self.cfg.max_pages):
            params = {
                "query": search["query"],
                "tags": search["tags"],
                "numericFilters": f"points>={search['min']},created_at_i>{int(start.timestamp())}",
                "typoTolerance": "false",
                "advancedSyntax": "true",
                "hitsPerPage": str(self.cfg.hits_per_page),
                "page": str(page),
            }
            if search["restrict"]:
                params["restrictSearchableAttributes"] = search["restrict"]
            data = self.client.get(f"{base}?{urlencode(params)}").json()
            out.extend(data.get("hits") or [])
            if page + 1 >= int(data.get("nbPages") or 0):
                break
        return out

    def _item(self, oid: str, hit: dict[str, Any], published: datetime | None) -> RawItem:
        hn_url = HN_ITEM.format(id=oid)
        linked = (hit.get("url") or "").strip() or None
        title = (hit.get("title") or "").strip() or f"Hacker News item {oid}"
        story = html_to_text(hit.get("story_text") or "")
        tags = [t for t in (hit.get("_tags") or []) if t in ("show_hn", "ask_hn", "launch_hn")]
        header = f"Hacker News post{' (Show HN)' if 'show_hn' in tags else ''}"
        header += f" linking to {linked}" if linked else ""
        text = "\n\n".join(p for p in (title, header, story) if p)
        return RawItem(
            url=linked or hn_url,
            canonical_url=canonical_url(hn_url),
            title=title,
            text=text,
            published_at=published,
            meta={
                "kind": KIND,
                "discovery": "hn",
                "text_source": "hn",
                "hn": {
                    "id": int(oid) if oid.isdigit() else oid,
                    "url": hn_url,
                    "points": int(hit.get("points") or 0),
                    "comments": int(hit.get("num_comments") or 0),
                    "author": hit.get("author"),
                    "tags": tags,
                    "matched": sorted(set(hit.get("_matched") or [])),
                },
                "linked_url": linked,
            },
        )

    def _add_linked_text(self, item: RawItem, result: CollectResult) -> None:
        linked = item.meta.get("linked_url")
        if not linked or not self.cfg.fetch_linked_page:
            return
        text, source = None, None
        try:
            if (repo := github_repo(linked)) is not None:
                text = fetch_readme(self.client, *repo, self.cfg.linked_page_chars)
                source = "readme"
            else:
                resp = self.client.get(linked)
                ctype = resp.headers.get("content-type", "")
                if resp.status_code != 304 and "html" in ctype:
                    text = self.extract(resp.text, linked)
                source = "article"
        except FetchError as e:
            logger.info("linked page fetch failed for %s: %s", linked, e)
            text = None
        if text:
            item.text += f"\n\nLinked page ({linked}):\n{text[: self.cfg.linked_page_chars]}"
            item.meta["text_source"] = source
        else:
            result.extract_failed += 1


def _ts(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return None
