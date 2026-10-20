"""RSS/Atom collector (feedparser); short items get the article text via trafilatura."""

import logging
from datetime import datetime
from typing import Any

import feedparser

from app.pipeline.collectors.base import CollectResult, KnownUrls, RawItem, SourceRef
from app.pipeline.collectors.http import FetchError, PoliteClient
from app.pipeline.normalize import canonical_url, html_to_text, to_utc

logger = logging.getLogger(__name__)

# Feed text shorter than this is treated as a teaser: fetch the article for the full text.
MIN_FULL_TEXT_CHARS = 500


class RSSCollector:
    def __init__(self, client: PoliteClient) -> None:
        self.client = client

    def collect(
        self, source: SourceRef, *, since: datetime, backfill: bool, known: KnownUrls
    ) -> CollectResult:
        result = CollectResult()
        # A backfill always re-reads the whole feed; a normal run may get a 304.
        resp = self.client.get(
            source.url,
            etag=None if backfill else source.http_etag,
            last_modified=None if backfill else source.http_last_modified,
        )
        if resp.status_code == 304:
            result.not_modified = True
            result.etag, result.last_modified = source.http_etag, source.http_last_modified
            return result
        result.etag = resp.headers.get("etag")
        result.last_modified = resp.headers.get("last-modified")

        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            raise FetchError(f"not a feed: {type(feed.bozo_exception).__name__}")

        keep_fragment = bool(source.options.get("keep_fragment", False))
        seen_urls: set[str] = set()
        collisions = 0
        for entry in feed.entries:
            link = entry.get("link")
            if not link:
                continue
            published = to_utc(entry.get("published_parsed") or entry.get("updated_parsed"))
            if not result.see(published, since):
                continue
            url_id = canonical_url(link, keep_fragment=keep_fragment)
            if url_id in seen_urls:
                # Several entries map to one URL (usually #anchors on one page): keep the first,
                # never let them overwrite each other. Fix with options.keep_fragment: true.
                collisions += 1
                continue
            seen_urls.add(url_id)
            item = RawItem(
                url=link,
                canonical_url=url_id,
                title=html_to_text(entry.get("title") or "").strip() or link,
                text=_entry_text(entry),
                published_at=published,
                meta={"text_source": "feed"},
            )
            if entry.get("id"):
                item.meta["feed_entry_id"] = entry["id"]

            if len(item.text) < MIN_FULL_TEXT_CHARS:
                if known.get(item.canonical_url) == "article":
                    # Already stored with full text; don't re-fetch the article every run.
                    result.skipped_known += 1
                    continue
                self._add_article_text(item, result)
            result.items.append(item)
        if collisions:
            result.notes.append(
                f"{collisions} entries shared a URL with an earlier entry and were skipped; "
                "if items are #anchors on one page, set options.keep_fragment: true"
            )
        return result

    def _add_article_text(self, item: RawItem, result: CollectResult) -> None:
        try:
            resp = self.client.get(item.url)
            text = _extract(resp.text, item.url) if resp.status_code != 304 else None
        except FetchError as e:
            logger.info("article fetch failed for %s: %s", item.url, e)
            text = None
        if text and len(text) > len(item.text):
            item.text = text
            item.meta["text_source"] = "article"
        else:
            result.extract_failed += 1
        if not item.text:
            item.text = item.title  # raw_text is required; the title is all we have


def _entry_text(entry: Any) -> str:
    contents = entry.get("content") or []
    html = max((c.get("value", "") for c in contents), key=len, default="")
    summary = entry.get("summary") or ""
    return html_to_text(html if len(html) >= len(summary) else summary)


def _extract(html: str, url: str) -> str | None:
    import trafilatura  # heavy import; only needed when an article is fetched

    return trafilatura.extract(
        html, url=url, include_comments=False, include_tables=True, favor_precision=True
    )
