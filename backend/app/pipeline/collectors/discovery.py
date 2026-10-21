"""Helpers shared by the discovery collectors (Hacker News, GitHub search, CNCF landscape).

Discovery items are ordinary signals with meta.kind = "discovery" and meta.discovery = hn |
github | cncf. Popularity numbers (HN points, GitHub stars) never go into the text: a changed
count must not look like an edited article (store.save_item would reset the enrichment).
"""

import logging
import re
from urllib.parse import urlsplit

from app.pipeline.collectors.http import FetchError, PoliteClient

logger = logging.getLogger(__name__)

KIND = "discovery"
RAW = "https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{name}"
README_NAMES = ("README.md", "readme.md", "README.rst", "README")
_REPO_PATH = re.compile(r"^/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$")
# github.com paths that are not repositories.
_NOT_OWNERS = frozenset(
    {"orgs", "topics", "features", "marketplace", "sponsors", "settings", "about", "collections"}
)


def github_repo(url: str | None) -> tuple[str, str] | None:
    """(owner, repo) for a github.com/<owner>/<repo> URL (deeper paths are cut to the repo)."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    if (parts.hostname or "").lower() not in ("github.com", "www.github.com"):
        return None
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2 or segments[0].lower() in _NOT_OWNERS:
        return None
    m = _REPO_PATH.match(f"/{segments[0]}/{segments[1]}")
    return (m.group(1), m.group(2)) if m else None


def repo_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}".lower()


def fetch_readme(client: PoliteClient, owner: str, repo: str, max_chars: int) -> str | None:
    """README text from raw.githubusercontent.com (no API quota), or None."""
    if max_chars <= 0:
        return None
    for name in README_NAMES:
        try:
            resp = client.get(RAW.format(owner=owner, repo=repo, name=name))
        except FetchError as e:
            if "HTTP 404" in str(e):
                continue
            logger.info("readme fetch failed for %s/%s: %s", owner, repo, e)
            return None
        text = resp.text.strip()
        if text:
            return text[:max_chars]
    return None
