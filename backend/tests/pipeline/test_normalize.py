from datetime import UTC, datetime, timedelta, timezone

import feedparser
import pytest

from app.pipeline.normalize import canonical_url, html_to_text, to_utc


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://Blog.Acme.TEST/post/", "https://blog.acme.test/post"),
        ("https://blog.acme.test/post#comments", "https://blog.acme.test/post"),
        (
            "https://blog.acme.test/post?utm_source=rss&utm_medium=feed&utm_campaign=x",
            "https://blog.acme.test/post",
        ),
        (
            "https://blog.acme.test/post?fbclid=1&gclid=2&mc_cid=3&_hsenc=4&id=7&a=1",
            "https://blog.acme.test/post?a=1&id=7",  # real params kept, sorted
        ),
        ("https://blog.acme.test:443/a//b/", "https://blog.acme.test/a/b"),
        ("http://blog.acme.test:8080/a", "http://blog.acme.test:8080/a"),
        ("https://user:pw@blog.acme.test/a", "https://blog.acme.test/a"),
        ("https://blog.acme.test/", "https://blog.acme.test"),
        ("https://blog.acme.test/p?q=", "https://blog.acme.test/p?q="),
    ],
)
def test_canonical_url(url, expected):
    assert canonical_url(url) == expected


def test_canonical_url_can_keep_the_fragment():
    url = "https://docs.acme.test/release-notes/?utm_source=x#July_24_2026"
    assert canonical_url(url) == "https://docs.acme.test/release-notes"
    assert canonical_url(url, keep_fragment=True) == (
        "https://docs.acme.test/release-notes#July_24_2026"
    )


def test_canonical_url_is_idempotent():
    url = "https://Blog.acme.test/x/?b=2&a=1&utm_term=z#frag"
    assert canonical_url(canonical_url(url)) == canonical_url(url)


def test_to_utc():
    plus2 = timezone(timedelta(hours=2))
    assert to_utc(datetime(2026, 10, 19, 14, 0, tzinfo=plus2)) == datetime(
        2026, 10, 19, 12, 0, tzinfo=UTC
    )
    assert to_utc(datetime(2026, 1, 1)) == datetime(2026, 1, 1, tzinfo=UTC)
    assert to_utc("2026-10-18T10:00:00Z") == datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
    parsed = feedparser.parse(
        b"<rss><channel><item><pubDate>Mon, 19 Oct 2026 14:00:00 +0200</pubDate>"
        b"</item></channel></rss>"
    )
    assert to_utc(parsed.entries[0].published_parsed) == datetime(2026, 10, 19, 12, tzinfo=UTC)
    assert to_utc(None) is None


def test_html_to_text_keeps_paragraphs():
    html = "<p>First   para &amp; more.</p><p>Second<br>line</p><script>x()</script>"
    assert html_to_text(html) == "First para & more.\n\nSecond\nline"
