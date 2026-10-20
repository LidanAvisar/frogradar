"""Content hash for exact-duplicate detection (signals.content_hash, page_snapshots.content_hash).

content_hash = sha256 hex of the normalized text, where normalizing means:
  1. strip HTML (tags removed, <script>/<style> bodies dropped, entities unescaped)
  2. collapse every run of whitespace to one space
  3. lowercase
  4. trim leading/trailing whitespace
So the same article fetched twice with different markup or spacing hashes the same.
"""

import hashlib
import re
from html.parser import HTMLParser

_WHITESPACE = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        # Tags separate words: "<p>a</p><p>b</p>" must not become "ab".
        self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def strip_html(text: str) -> str:
    parser = _TextExtractor()
    parser.feed(text)
    parser.close()
    return "".join(parser.parts)


def normalize_text(text: str) -> str:
    return _WHITESPACE.sub(" ", strip_html(text)).lower().strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()
