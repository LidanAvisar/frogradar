"""Evidence-quote check: the quote must appear verbatim in the signal's raw_text.

"Verbatim" after normalization only: Unicode NFKC, typographic quotes/dashes to ASCII, and all
whitespace collapsed. Case, words and punctuation must match, so a paraphrase or an invented
sentence fails. The check is code, not a model, so injected text can't talk its way past it.

Repair (D39): a near-miss quote (small typos, a dropped word) is replaced by the real source span
it was copied from, before any LLM retry. Only one sentence or two adjacent sentences qualify, and
the span must both resemble the quote (difflib ratio >= threshold) and cover it (>= threshold of
the quote's characters found in the span). Coverage stops a quote stitched from distant sentences
being "repaired" to just its longest part. The numbers in quote and span must be identical.
"""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

_ASCII = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
        "“": '"', "”": '"', "„": '"', "″": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    }
)  # fmt: skip
_STRIP = " \t\n\"'"
# Sentence end: . ! ? (optionally followed by a closing quote/bracket) and whitespace.
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]?\s+")
_MIN_QUOTE_CHARS = 10


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_ASCII)
    return " ".join(text.split())


def quote_in_text(quote: str, text: str) -> bool:
    q = normalize(quote).strip(_STRIP)
    return len(q) >= _MIN_QUOTE_CHARS and q in normalize(text)


def quote_error(quote: str, *, diff: bool = False) -> str:
    where = (
        "one line (or part of one) exactly as it appears in an [after] section of the diff"
        if diff
        else "one or two complete sentences exactly as they appear inside the source_text block"
    )
    return (
        f"evidence_quote is not a verbatim excerpt of the source text: {quote[:200]!r}. "
        f"Copy {where} (no paraphrasing, no ellipses, no added words). "
        "Keep every other field as it was."
    )


def sentences(text: str) -> list[str]:
    """Split normalized text into sentences (also on line breaks of the original text)."""
    out: list[str] = []
    for line in unicodedata.normalize("NFKC", text).translate(_ASCII).splitlines():
        line = " ".join(line.split())
        out.extend(s.strip() for s in _SENTENCE_END.split(line) if s.strip())
    return out


@dataclass(frozen=True)
class QuoteRepair:
    span: str  # the real source text (normalized), one or two adjacent sentences
    similarity: float  # difflib ratio quote vs span
    coverage: float  # share of the quote's characters found in the span


def _score(quote: str, span: str) -> tuple[float, float]:
    m = SequenceMatcher(None, quote, span, autojunk=False)
    matched = sum(b.size for b in m.get_matching_blocks())
    return m.ratio(), matched / len(quote)


def best_span(quote: str, text: str) -> QuoteRepair | None:
    """The single sentence or adjacent pair that best matches the quote (by ratio)."""
    q = normalize(quote).strip(_STRIP)
    if len(q) < _MIN_QUOTE_CHARS:
        return None
    sents = sentences(text)
    spans = sents + [f"{a} {b}" for a, b in zip(sents, sents[1:], strict=False)]
    best: QuoteRepair | None = None
    for span in spans:
        # Cheap upper bound first: most spans are nowhere near.
        if SequenceMatcher(None, q, span, autojunk=False).real_quick_ratio() < 0.5:
            continue
        ratio, coverage = _score(q, span)
        if best is None or ratio > best.similarity:
            best = QuoteRepair(span, ratio, coverage)
    return best


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def numbers(text: str) -> list[str]:
    return _NUMBER.findall(normalize(text))


def repair_quote(quote: str, text: str, threshold: float) -> QuoteRepair | None:
    """A real source span to use instead of a near-miss quote, or None (retry / flag instead).

    A repair never changes a number: "$1,620" quoted against a source line saying "$1,950" is
    ~94% similar, but swapping it would change what the quote proves.
    """
    found = best_span(quote, text)
    if found is None or found.similarity < threshold or found.coverage < threshold:
        return None
    if numbers(found.span) != numbers(quote):
        return None
    if len(found.span) < _MIN_QUOTE_CHARS or not quote_in_text(found.span, text):
        return None
    return found
