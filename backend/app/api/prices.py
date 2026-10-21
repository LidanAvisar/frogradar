"""Deterministic before → after price pairs from a stored page diff (D48). No LLM.

The diff text (signals.raw_text of a page_diff signal, rendered by app.pipeline.page_diff) has
"## Change" hunks with [before] / [after] / [context] blocks. A pair is returned only when it is
unambiguous:

- Labeled lines ("[Plan > Pro Edition] Starting at $1,620 / year"): a before line pairs with the
  after line whose label is the same, or – if no exact match – with the only after label that
  contains all of the before label's segments in order (and that after label has no other
  candidate). Two candidates (e.g. Cloud > Pro and Self-Hosted > Pro) → no pair.
- Unlabeled lines: only when the hunk has exactly one priced line on each side.
- A priced line holds exactly one money amount; lines with several amounts are comparison rows or
  usage tables and are skipped. Equal prices are not changes.

Only the label and the two short price strings leave this module, never whole lines (raw_text rule).
"""

import re
from dataclasses import dataclass

from app.api.schemas import PriceChange

_AMOUNT = re.compile(r"\$\s?(?:\d[\d,]*(?:\.\d+)?|\.\d+)")
_PRICE = re.compile(
    r"\$\s?(?:\d[\d,]*(?:\.\d+)?|\.\d+)"
    r"(?:\s*/\s*(?:year|yr|month|mo)\b"
    r"|\s+per\s+(?:user/month|user/year|user|month|year)\b)?"
)
_LABELED = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
_BLOCK = re.compile(r"^\[(before|after|context)\]$")
_MAX_LABEL = 120


@dataclass(frozen=True)
class _Priced:
    label: tuple[str, ...] | None
    price: str


def _priced(line: str) -> _Priced | None:
    label: tuple[str, ...] | None = None
    text = line
    m = _LABELED.match(line)
    if m and not _BLOCK.match(line):
        label = tuple(p.strip() for p in m.group(1).split(">") if p.strip())
        text = m.group(2)
    if len(_AMOUNT.findall(text)) != 1:
        return None
    price = _PRICE.search(text)
    if price is None:
        return None
    return _Priced(label, re.sub(r"\s+", " ", price.group(0)).strip())


def _hunks(diff_text: str) -> list[tuple[list[str], list[str]]]:
    """[(before lines, after lines)] per "## Change" hunk."""
    hunks: list[tuple[list[str], list[str]]] = []
    side: str | None = None
    for raw in diff_text.splitlines():
        line = raw.strip()
        if line.startswith("## Change"):
            hunks.append(([], []))
            side = None
            continue
        if not hunks:
            continue
        block = _BLOCK.match(line)
        if block:
            side = block.group(1)
            continue
        if not line or line in ("(nothing)", "(removed)"):
            continue
        if side == "before":
            hunks[-1][0].append(line)
        elif side == "after":
            hunks[-1][1].append(line)
    return hunks


def _contains_in_order(short: tuple[str, ...], long: tuple[str, ...]) -> bool:
    it = iter(long)
    return all(any(seg == x for x in it) for seg in short)


def _label_text(label: tuple[str, ...]) -> str:
    # Drop a generic first segment ("Product Packages") when there is more to say.
    parts = label[1:] if len(label) > 2 else label
    return " > ".join(parts)[:_MAX_LABEL]


def _pair_labeled(before: list[_Priced], after: list[_Priced]) -> list[PriceChange]:
    after_labels = {p.label for p in after}
    by_label: dict[tuple[str, ...], list[_Priced]] = {}
    for p in after:
        by_label.setdefault(p.label, []).append(p)  # type: ignore[arg-type]

    def target(lbl: tuple[str, ...]) -> tuple[str, ...] | None:
        if lbl in after_labels:
            return lbl
        cands = [a for a in after_labels if a and _contains_in_order(lbl, a)]
        return cands[0] if len(cands) == 1 else None

    before_labels = [p.label for p in before]
    out: list[PriceChange] = []
    for b in before:
        assert b.label is not None
        tgt = target(b.label)
        if tgt is None:
            continue
        # The after label must not be claimed by another before label too.
        if sum(1 for lbl in before_labels if lbl is not None and target(lbl) == tgt) != 1:
            continue
        # One priced line per label on each side, otherwise rows are ambiguous.
        if before_labels.count(b.label) != 1 or len(by_label[tgt]) != 1:
            continue
        a = by_label[tgt][0]
        if a.price != b.price:
            out.append(PriceChange(label=_label_text(tgt), before=b.price, after=a.price))
    return out


def price_changes(diff_text: str) -> list[PriceChange]:
    changes: list[PriceChange] = []
    for before_lines, after_lines in _hunks(diff_text):
        before = [p for p in map(_priced, before_lines) if p]
        after = [p for p in map(_priced, after_lines) if p]
        if not before or not after:
            continue
        b_lab = [p for p in before if p.label]
        a_lab = [p for p in after if p.label]
        if b_lab and a_lab:
            changes.extend(_pair_labeled(b_lab, a_lab))
        elif (
            not b_lab
            and not a_lab
            and len(before) == len(after) == 1
            and before[0].price != after[0].price
        ):
            changes.append(PriceChange(label="", before=before[0].price, after=after[0].price))
    seen: set[tuple[str, str, str]] = set()
    unique = []
    for c in changes:
        key = (c.label, c.before, c.after)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique
