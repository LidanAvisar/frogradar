"""Quote repair (D39): a near-miss quote becomes the real source span, but only when it matches
one sentence or two adjacent sentences closely (similarity AND coverage >= threshold)."""

from app.pipeline.quotes import best_span, quote_in_text, repair_quote, sentences

T = 0.9
TEXT = (
    "GitHub is introducing a new package firewall capability. The package firewall sits between "
    "endpoints like Actions runners or coding agents and the upstream package repositories, "
    "inspecting both metadata requests and package downloads for signs of malicious activity. "
    "The package firewall is available today in public preview for all GitHub Enterprise Cloud "
    "customers.\n"
    "Pricing is based on metered usage, similar to Actions billing. "
    "Currently the package firewall only supports npm packages. How to use it is documented."
)
A = (
    "The package firewall sits between endpoints like Actions runners or coding agents and the "
    "upstream package repositories, inspecting both metadata requests and package downloads for "
    "signs of malicious activity."
)
C = "Currently the package firewall only supports npm packages."


def test_sentences_split_on_ends_and_lines():
    parts = sentences(TEXT)
    assert parts[1] == A and C in parts and len(parts) == 6


def test_small_drift_in_one_sentence_is_repaired_to_the_source_span():
    quote = A.replace("coding agents", "AI coding agents").replace("signs of", "sign of")
    assert not quote_in_text(quote, TEXT)
    fixed = repair_quote(quote, TEXT, T)
    assert fixed is not None and fixed.span == A
    assert fixed.similarity >= T and fixed.coverage >= T
    assert quote_in_text(fixed.span, TEXT)


def test_two_adjacent_sentences_with_drift_are_repaired_to_the_pair():
    b = (
        "The package firewall is available today in public preview for all GitHub Enterprise "
        "Cloud customers."
    )
    quote = f"{A} {b}".replace("today", "now")
    fixed = repair_quote(quote, TEXT, T)
    assert fixed is not None and fixed.span == f"{A} {b}"


def test_stitched_from_distant_sentences_is_not_repaired():
    # The #1046 case: sentence A + sentence C, ~two sentences apart. A alone scores ~0.89 ratio
    # and only ~81% coverage of the quote, so it must not be "repaired" to A.
    quote = f"{A} {C}"
    assert not quote_in_text(quote, TEXT)
    best = best_span(quote, TEXT)
    assert best is not None and best.coverage < T
    assert repair_quote(quote, TEXT, T) is None


def test_coverage_bounds_how_much_of_the_quote_may_be_dropped():
    # Up to 10% of the quote may be missing from the span (typo-level drift)...
    tiny_tail = f"{A} Only npm."
    fixed = repair_quote(tiny_tail, TEXT, T)
    assert fixed is not None and fixed.span == A and fixed.coverage >= T
    # ...a whole extra sentence is not drift: ratio alone (~0.9) would accept it, coverage won't.
    stitched = f"{A} Pricing is per seat today."
    best = best_span(stitched, TEXT)
    assert best is not None and best.span == A and best.coverage < T
    assert repair_quote(stitched, TEXT, T) is None


def test_unrelated_quote_is_not_repaired():
    assert repair_quote("Sonatype is the market leader in repository firewalls.", TEXT, T) is None
    assert repair_quote("short", TEXT, T) is None


def test_a_repair_never_changes_a_number():
    text = "Pricing\nPro Edition Starting at $1,950 / year + consumption\nEnterprise: contact us."
    old_price = "Pro Edition Starting at $1,620 / year + consumption"
    best = best_span(old_price, text)
    assert best is not None and best.similarity >= T and best.coverage >= T  # looks "close"
    assert repair_quote(old_price, text, T) is None
    typo = "Pro Edition starting at $1,950 / year + consumption"
    fixed = repair_quote(typo, text, T)
    assert fixed is not None and fixed.span.endswith("$1,950 / year + consumption")
