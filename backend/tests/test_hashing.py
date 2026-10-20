import hashlib

from app.pipeline.hashing import content_hash, normalize_text


def test_normalize_strips_html_whitespace_and_case():
    html = "<html><head><style>p{color:red}</style><script>track()</script></head>\n<body>"
    html += "<h1>JFrog   Launches</h1><p>New&nbsp;<b>Feature</b> &amp; more</p></body></html>"
    assert normalize_text(html) == "jfrog launches new feature & more"


def test_tags_separate_words():
    assert normalize_text("<p>alpha</p><p>beta</p>") == "alpha beta"


def test_same_content_different_markup_same_hash():
    a = "<div><p>Snyk acquires   Example Corp.</p></div>"
    b = "Snyk ACQUIRES Example Corp.\n"
    assert content_hash(a) == content_hash(b)


def test_different_content_different_hash():
    assert content_hash("price is $10") != content_hash("price is $12")


def test_hash_is_sha256_hex_of_normalized_text():
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert content_hash("  <b>Hello</b>\tWORLD ") == expected
    assert len(expected) == 64
