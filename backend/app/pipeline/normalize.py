"""Normalization shared by all collectors: canonical URLs, UTC timestamps, HTML → plain text."""

import re
from datetime import UTC, datetime
from time import struct_time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.pipeline.hashing import strip_html

# Query parameters that only track the click, never change the content.
_TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "igshid", "twclid",
    "mkt_tok", "ref_src", "ref_url", "spm", "_ga", "_gl", "oly_anon_id", "oly_enc_id",
    "vero_conv", "vero_id", "trk", "trkcampaign", "sc_campaign", "sc_channel",
}  # fmt: skip
_TRACKING_PREFIXES = ("utm_", "mc_", "_hs", "pk_", "hsa_", "piwik_", "matomo_")
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _is_tracking(param: str) -> bool:
    p = param.lower()
    return p in _TRACKING_PARAMS or p.startswith(_TRACKING_PREFIXES)


def canonical_url(url: str, *, keep_fragment: bool = False) -> str:
    """Stable identity for a URL.

    Lowercase scheme and host, drop credentials, default port, fragment, tracking parameters
    (utm_*, fbclid, …) and the trailing slash; sort the remaining query parameters.
    keep_fragment: for feeds whose items are #anchors on one page (release notes).
    """
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    netloc = host if parts.port in (None, _DEFAULT_PORTS.get(scheme)) else f"{host}:{parts.port}"
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/")
    query = parse_qsl(parts.query, keep_blank_values=True)
    params = [(k, v) for k, v in query if not _is_tracking(k)]
    fragment = parts.fragment if keep_fragment else ""
    return urlunsplit((scheme, netloc, path, urlencode(sorted(params)), fragment))


def to_utc(value: datetime | struct_time | str | None) -> datetime | None:
    """Datetime in UTC. Naive datetimes and struct_times (feedparser) are taken as UTC."""
    if value is None:
        return None
    if isinstance(value, struct_time):
        return datetime(*value[:6], tzinfo=UTC)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


_PARAGRAPH_END = re.compile(r"(?i)<\s*(/p|/div|/h[1-6]|/blockquote|/pre|/ul|/ol|/table)\b[^>]*>")
_LINE_END = re.compile(r"(?i)<\s*(br|/li|/tr)\b[^>]*>")
_SPACES = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n\s*\n\s*")


def html_to_text(html: str) -> str:
    """Plain text that keeps paragraph breaks (feed summaries/content are HTML)."""
    text = strip_html(_LINE_END.sub("\n", _PARAGRAPH_END.sub("\n\n", html)))
    lines = (_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
