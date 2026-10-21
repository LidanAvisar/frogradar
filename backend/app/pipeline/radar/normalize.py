"""Name keys and URL hosts for radar entity resolution (code, no LLM).

name_key: NFKC, case-folded, "owner/" of a GitHub repo and a trailing domain suffix (.com, .io,
.dev, ...) removed, everything but letters and digits removed, then trailing corporate words
(inc, ltd, labs, security, ...) removed while at least 3 characters remain.
Deliberately conservative: a wrong key merges two different players, a missed one only leaves a
duplicate the fuzzy pass or the analyst can still catch.
    "Endor Labs" / "EndorLabs" → "endor";  "socket.dev" / "Socket Security" → "socket";
    "github.com/regshield/regshield" → "regshield"
"""

import re
import unicodedata
from urllib.parse import urlsplit

_DOMAIN_SUFFIX = re.compile(r"\.(com|io|dev|ai|sh|app|so|org|net|co|cloud|tech|run)$")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")
# Trailing words that don't change which player is meant. Not "lab" (gitlab → git) and not
# "io" / "dev" without the dot (trivio → triv): the domain-suffix rule handles "x.io".
_CORPORATE = (
    "corporation", "technologies", "software", "security", "systems", "limited",
    "labs", "corp", "gmbh", "inc", "ltd", "llc", "hq",
)  # fmt: skip
MIN_KEY = 3

# Hosts shared by many unrelated projects: never evidence that two candidates are the same.
SHARED_HOSTS = frozenset(
    {
        "github.com", "gitlab.com", "bitbucket.org", "medium.com", "substack.com", "dev.to",
        "npmjs.com", "www.npmjs.com", "pypi.org", "youtube.com", "www.youtube.com", "x.com",
        "twitter.com", "linkedin.com", "www.linkedin.com", "news.ycombinator.com",
        "huggingface.co", "docs.google.com", "notion.site", "vercel.app", "netlify.app",
        "landscape.cncf.io", "crunchbase.com", "www.crunchbase.com", "producthunt.com",
        "www.producthunt.com", "raw.githubusercontent.com", "gist.github.com",
    }
)  # fmt: skip


def name_key(name: str) -> str:
    text = unicodedata.normalize("NFKC", name or "").casefold().strip()
    if "/" in text and " " not in text:
        text = text.rstrip("/").rsplit("/", 1)[-1]  # owner/repo or a URL path → repo
    text = _DOMAIN_SUFFIX.sub("", text)
    key = _NON_ALNUM.sub("", text)
    stripped = True
    while stripped:  # "Socket Security Inc" → "socketsecurity" → "socket"
        stripped = False
        for word in _CORPORATE:
            if key.endswith(word) and len(key) - len(word) >= MIN_KEY:
                key, stripped = key[: -len(word)], True
                break
    return key


def host(url: str | None) -> str | None:
    if not url:
        return None
    h = (urlsplit(url.strip()).hostname or "").lower()
    return h or None


def site_host(url: str | None) -> str | None:
    """Host without "www.", or None for shared hosts (github.com, medium.com, ...)."""
    h = host(url)
    if h is None or h in SHARED_HOSTS:
        return None
    return h.removeprefix("www.")


def repo_slug(url: str | None) -> str | None:
    """ "owner/repo" (lower case) of a github.com URL."""
    from app.pipeline.collectors.discovery import github_repo

    parsed = github_repo(url)
    return f"{parsed[0]}/{parsed[1]}".lower() if parsed else None
