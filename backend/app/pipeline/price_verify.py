"""Code check of the price changes the model read from a page diff (enrich_page_diff.v4, D52).

The model proposes {label, before, after}; a change is accepted only if, in ONE "## Change" hunk:
- `before` occurs in a [before] line and `after` in an [after] line (normalized: case, spaces,
  "$ 1" → "$1", typographic dashes/quotes), never only in [context] lines;
- before ≠ after, and at least one side holds a digit or currency sign;
- both sides use the same billing unit: period (per month vs per year) and per-seat vs flat, when
  stated. "$135 per month" → "$1,620 / year" is the same price shown differently, not a change.
The label comes from the page, not the model: the section path of the matching [after] line
(or of the [before] line if the after line has none). Matches in lines with two or more
different section paths → ambiguous → dropped. Diffs without section paths (GitHub, v1 format)
use the model's label only if it occurs in that hunk, else "".

Only short strings (label, two prices ≤ 60 chars) are stored, never whole lines (raw_text rule).
"""

import re
from typing import Any

from app.pipeline.page_diff import DiffLine, diff_hunks, section_label
from app.pipeline.schemas import ProposedPriceChange

_DASHES = str.maketrans({"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"', " ": " "})
_CURRENCY_SPACE = re.compile(r"([$€£])\s+(?=[\d.])")
_HAS_AMOUNT = re.compile(r"[\d$€£]")
_PERIOD = re.compile(
    r"(?P<month>\bper month\b|/\s?mo(?:nth)?\b|\bmonthly\b|\ba month\b)"
    r"|(?P<year>\bper year\b|/\s?y(?:ea)?r\b|\byearly\b|\ba year\b|\bannual(?:ly)?\b)"
)
_SEAT = re.compile(r"\b(?:per |/\s?)(?:user|seat|developer|committer|contributor)s?\b")


def billing_unit(text: str) -> tuple[str | None, bool]:
    """(period of the FIRST period phrase – "per month/billed annually" is a monthly price –,
    per-seat?) of a normalized price string."""
    m = _PERIOD.search(text)
    period = m.lastgroup if m else None
    return period, bool(_SEAT.search(text))


def normalize(text: str) -> str:
    t = text.translate(_DASHES).casefold()
    t = re.sub(r"\s+", " ", t).strip()
    return _CURRENCY_SPACE.sub(r"\1", t)


def _containing(lines: list[DiffLine], side: str, needle: str) -> list[DiffLine]:
    return [ln for ln in lines if ln.side == side and needle in normalize(ln.text)]


def _check(
    change: ProposedPriceChange, hunks: list[list[DiffLine]]
) -> tuple[dict[str, str] | None, str]:
    before, after = normalize(change.before), normalize(change.after)
    if not before or not after:
        return None, "empty"
    if before == after:
        return None, "no_change"
    if not (_HAS_AMOUNT.search(before) or _HAS_AMOUNT.search(after)):
        return None, "not_a_price"
    (b_period, b_seat), (a_period, a_seat) = billing_unit(before), billing_unit(after)
    if (b_period and a_period and b_period != a_period) or b_seat != a_seat:
        return None, "unit_mismatch"
    reason = "before_not_in_before"
    for hunk in hunks:
        b_lines = _containing(hunk, "before", before)
        if not b_lines:
            continue
        a_lines = _containing(hunk, "after", after)
        if not a_lines:
            reason = "after_not_in_same_hunk"
            continue
        paths = {ln.section for ln in a_lines if ln.section} or {
            ln.section for ln in b_lines if ln.section
        }
        if len(paths) > 1:
            reason = "ambiguous_section"
            continue
        if paths:
            label = section_label(next(iter(paths)), 80)
        else:
            hunk_text = normalize(" ".join(ln.text for ln in hunk))
            model_label = normalize(change.label)
            label = change.label.strip() if model_label and model_label in hunk_text else ""
        return {"label": label, "before": change.before.strip(), "after": change.after.strip()}, ""
    return None, reason


def verify_price_changes(
    proposed: list[ProposedPriceChange], diff_text: str
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """(accepted, dropped) – dropped items keep the model's values plus a reason code."""
    hunks = diff_hunks(diff_text)
    accepted: list[dict[str, str]] = []
    dropped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for change in proposed:
        ok, reason = _check(change, hunks)
        if ok is None:
            dropped.append({**change.model_dump(), "reason": reason})
            continue
        key = (ok["label"], normalize(ok["before"]), normalize(ok["after"]))
        if key in seen:
            continue
        seen.add(key)
        accepted.append(ok)
    return accepted, dropped
