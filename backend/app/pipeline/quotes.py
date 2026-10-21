"""Evidence-quote check: the quote must appear verbatim in the signal's raw_text.

"Verbatim" after normalization only: Unicode NFKC, typographic quotes/dashes to ASCII, and all
whitespace collapsed. Case, words and punctuation must match, so a paraphrase or an invented
sentence fails. The check is code, not a model, so injected text can't talk its way past it.
"""

import unicodedata

_ASCII = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
        "“": '"', "”": '"', "„": '"', "″": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    }
)  # fmt: skip
_STRIP = " \t\n\"'"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_ASCII)
    return " ".join(text.split())


def quote_in_text(quote: str, text: str) -> bool:
    q = normalize(quote).strip(_STRIP)
    return len(q) >= 10 and q in normalize(text)


def quote_error(quote: str) -> str:
    return (
        f"evidence_quote is not a verbatim excerpt of the source text: {quote[:200]!r}. "
        "Copy one or two complete sentences exactly as they appear inside the source_text block "
        "(no paraphrasing, no ellipses, no added words). Keep every other field as it was."
    )
