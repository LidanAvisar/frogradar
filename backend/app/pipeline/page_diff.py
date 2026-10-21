"""Page diff logic (no I/O): extract → normalize → diff → classify → signal text.

Used by the live page_diff collector and by the Wayback backfill (D40, D41).

- Text: trafilatura main-content extraction, or the elements matching source.options.selector
  (CSS). Before extraction the HTML is annotated with the page's structure: h2-h4 headings, tab
  panels (aria-labelledby → the tab's label) and source.options.sections ({selector, label}, e.g.
  a Cloud / Self-Hosted toggle). Normalized per line: NFKC, whitespace collapsed, noise patterns
  (dates, ©, "3 days ago") removed, cookie/nav lines dropped, runs of very short lines joined
  ("$", "19", "per user" → "$ 19 per user"), consecutive repeats dropped. Prices are never touched.
  Structure becomes path lines "# Nexus Repository > Cloud > Pro Edition" (never joined with
  other lines), so two "Pro Edition" prices under different products stay distinguishable.
  NORMALIZER_VERSION is stored on each snapshot; only same-version snapshots are compared.
- Diff: line-based (difflib). Lines that appear on both sides (moved/reordered) don't count.
- Classification of a pair of snapshots:
    no_change     identical normalized text
    noise         changed characters below min_changed_chars
    changed       a normal change → one signal with the changed sections
    restructured  changed-line ratio above restructure_ratio (a redesign): no normal signal.
                  If the price lines alone (currency, per user/month…) differ, the signal is built
                  from those instead (diff_mode = price_lines); otherwise no signal at all.
- Signal text: a header (with a format note) plus "## Change N" blocks with [before] / [after] /
  [context] sections, and a "Moved between sections" list for features that changed tiers. The
  evidence quote comes from the [after] text; only a removal-only diff (no [after] text at all)
  quotes the removed [before] text instead (quote_side = "before").
"""

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from app.core.catalog import PageDiffSettings

_BEFORE, _AFTER, _CONTEXT = "[before]", "[after]", "[context]"
_MARKERS = (_BEFORE, _AFTER, _CONTEXT)
# 1 = plain text (S7 first version, no path lines); 2 = structure path lines (D40 addendum).
NORMALIZER_VERSION = 2
PATH_PREFIX = "# "  # a normalized line naming the page section the following lines belong to
_SECTION_RE = re.compile(r"@@section (.+?)@@")
_END_RE = re.compile(r"@@endsection@@")
_HEADING_RE = re.compile(r"@@heading (.*?)@@")
_ANY_MARKER = re.compile(r"@@(?:section .+?|endsection|heading .*?)@@")
# Put in every diff header: both the writer and the verifier misread a context line listing two
# plans' prices side by side ("$4 per user/month $21 per user/month") as a price change.
FORMAT_NOTE = (
    "Format: [before] = old lines, [after] = the new lines that replaced them, [context] = "
    "unchanged neighbouring lines for orientation. Only [before] -> [after] is a change. A line "
    "listing several prices is a comparison-table row (one price per plan), not a price change. "
    "A line starting with '# ' names the page section (product > option > plan) of the lines "
    "after it; '[a > b] text' = text from that section."
)


class ExtractionError(ValueError):
    """The page yielded no usable text (JS shell, error page, selector matched nothing)."""


# ---------- extraction + normalization ----------


def extract_text(
    html: str, selector: str | None = None, sections: list[dict[str, str]] | None = None
) -> str:
    """Main text of a page (the CSS selector's elements if given, else trafilatura), with
    @@section/@@heading markers for the page structure (see annotate)."""
    import lxml.html

    try:
        doc = lxml.html.fromstring(html)
    except Exception as e:  # noqa: BLE001 - empty or broken HTML
        raise ExtractionError(f"unparseable HTML: {e}") from None
    for bad in doc.xpath("//script|//style|//noscript"):
        bad.drop_tree()
    annotate(doc, sections or [])
    if selector:
        nodes = doc.cssselect(selector)
        if not nodes:
            raise ExtractionError(f"selector {selector!r} matched nothing")
        return "\n".join(_node_lines(n) for n in nodes)

    import trafilatura  # heavy import

    text = trafilatura.extract(
        lxml.html.tostring(doc, encoding="unicode"),
        favor_recall=True,
        include_tables=True,
        include_comments=False,
        deduplicate=False,
    )
    if not text:
        raise ExtractionError("trafilatura found no main content (the page may need JavaScript)")
    return text


def _clean(text: str) -> str:
    return " ".join(_ANY_MARKER.sub(" ", text).split())


def annotate(doc: Any, sections: list[dict[str, str]]) -> None:
    """Mark the page structure in place with text markers that survive extraction:
    h2-h4 → "@@heading <text>@@"; a tab panel (aria-labelledby) or an element matching a
    configured section selector is wrapped in "@@section <label>@@" … "@@endsection@@"."""
    from lxml.html import Element

    def wrap(el: Any, label: str) -> None:
        label = _clean(label)
        if not label:
            return
        start, end = Element("h2"), Element("h2")
        start.text, end.text = f"@@section {label}@@", "@@endsection@@"
        el.insert(0, start)
        el.append(end)

    for h in doc.xpath("//h2|//h3|//h4"):  # h1 = the page title: on every path, adds nothing
        text = _clean(h.text_content())
        for child in list(h):
            h.remove(child)
        h.text = f"@@heading {text}@@"
    for panel in doc.xpath("//*[@aria-labelledby]"):
        ref = doc.get_element_by_id(panel.get("aria-labelledby"), None)
        if ref is not None:
            wrap(panel, ref.text_content())
    for section in sections:
        for el in doc.cssselect(section["selector"]):
            wrap(el, section["label"])


def _node_lines(node: Any) -> str:
    """Text of an element with block elements on their own lines."""
    blocks = {"p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "section"}
    parts: list[str] = []
    for el in node.iter():
        if el.text:
            parts.append(el.text)
        if isinstance(el.tag, str) and el.tag.lower() in blocks:
            parts.append("\n")
        if el.tail:
            parts.append(el.tail)
    return "".join(parts)


@dataclass(frozen=True)
class Normalizer:
    noise: list[re.Pattern[str]]
    drop: list[re.Pattern[str]]
    short_line_chars: int
    prices: list[re.Pattern[str]] = field(default_factory=list)

    @classmethod
    def from_settings(cls, cfg: PageDiffSettings) -> "Normalizer":
        flags = re.IGNORECASE
        return cls(
            noise=[re.compile(p, flags) for p in cfg.noise_patterns],
            drop=[re.compile(p, flags) for p in cfg.drop_line_patterns],
            short_line_chars=cfg.short_line_chars,
            prices=[re.compile(p, flags) for p in cfg.price_patterns],
        )

    def _clean_line(self, raw: str) -> str:
        line = raw.replace("|", " ")  # trafilatura table cell separators
        for p in self.noise:
            line = p.sub("", line)
        return " ".join(line.split()).strip(" -·•#")

    def lines(self, text: str) -> list[str]:
        out: list[str] = []
        short_run: list[str] = []
        # Structure: a stack of [section label, heading inside it]; index 0 = outside any section.
        stack: list[list[str | None]] = [[None, None]]
        emitted: str | None = None

        def flush() -> None:
            if short_run:
                out.append(" ".join(short_run))
                short_run.clear()

        def path() -> str:
            parts = [p for frame in stack for p in frame if p]
            return " > ".join(parts)

        def emit_path() -> None:
            nonlocal emitted
            current = path()
            if current and current != emitted:
                flush()
                out.append(PATH_PREFIX + current)
            emitted = current

        for raw in unicodedata.normalize("NFKC", text).splitlines():
            stripped = raw.strip()
            if m := _SECTION_RE.fullmatch(stripped):
                flush()
                stack.append([self._clean_line(m.group(1)) or None, None])
                continue
            if _END_RE.fullmatch(stripped):
                flush()
                if len(stack) > 1:
                    stack.pop()
                continue
            if m := _HEADING_RE.fullmatch(stripped):
                heading = self._clean_line(m.group(1))
                # A heading that states a price stays a normal line (prefixes carry no prices).
                if heading and not any(p.search(heading) for p in self.prices):
                    if not any(p.search(heading) for p in self.drop):
                        flush()
                        stack[-1][1] = heading
                    continue
                raw = heading
            line = self._clean_line(_ANY_MARKER.sub(" ", raw))
            if not line or any(p.search(line) for p in self.drop):
                continue
            emit_path()
            if len(line) < self.short_line_chars:
                short_run.append(line)
                continue
            flush()
            out.append(line)
        flush()
        # Consecutive repeats (responsive layouts render the same block twice in a row).
        return [ln for i, ln in enumerate(out) if i == 0 or ln != out[i - 1]]

    def normalize(self, text: str) -> str:
        return "\n".join(self.lines(text))


def text_hash(normalized: str) -> str:
    """sha256 of the normalized text (case kept: "Free" → "free" is a change worth seeing)."""
    return hashlib.sha256(normalized.encode()).hexdigest()


# ---------- diff ----------


@dataclass
class Hunk:
    before: list[str]
    after: list[str]
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TierMove:
    """A line that moved from one priced section (tier) to another: a feature changing tiers."""

    line: str
    from_heading: str  # the price line heading its old section (old page)
    to_heading: str  # ... and its new section (new page)


@dataclass
class LineDiff:
    hunks: list[Hunk]
    removed: int  # changed lines (moved lines excluded)
    added: int
    moved: int  # lines present on both sides at different positions (ignored)
    tier_moves: list[TierMove]  # moved between priced sections (counted as changes)
    changed_chars: int  # non-whitespace characters changed (character-level edit size)
    total_lines: int  # lines before + lines after
    lines_before: int
    lines_after: int
    touches_prices: bool = False  # a changed line states a price or plan limit

    @property
    def changed_ratio(self) -> float:
        return (self.removed + self.added) / self.total_lines if self.total_lines else 0.0

    def stats(self) -> dict[str, Any]:
        return {
            "lines_before": self.lines_before,
            "lines_after": self.lines_after,
            "removed": self.removed,
            "added": self.added,
            "moved": self.moved,
            "tier_moves": len(self.tier_moves),
            "changed_chars": self.changed_chars,
            "changed_line_ratio": round(self.changed_ratio, 3),
            "touches_prices": self.touches_prices,
            "hunks": len(self.hunks),
        }


def _edit_size(before: list[str], after: list[str]) -> int:
    """Changed non-whitespace characters between two blocks (a character-level diff, so a
    one-word edit in a long line counts as one word, not as the whole line twice)."""
    a, b = "".join("".join(before).split()), "".join("".join(after).split())
    if not a or not b:
        return len(a) + len(b)
    matched = sum(m.size for m in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
    return len(a) + len(b) - 2 * matched


def _is_price(line: str, patterns: list[re.Pattern[str]]) -> bool:
    return not line.startswith(PATH_PREFIX) and any(p.search(line) for p in patterns)


def _path_before(lines: list[str], idx: int) -> str | None:
    """The section path line in force at index idx (searching upwards)."""
    for k in range(min(idx, len(lines)) - 1, -1, -1):
        if lines[k].startswith(PATH_PREFIX):
            return lines[k]
    return None


def _sections(lines: list[str], patterns: list[re.Pattern[str]]) -> list[str | None]:
    """Per line: "<key>\t<name>" of the section it belongs to (None = before any section).

    With structure path lines (normalizer v2) a section is its path ("Nexus Repository > Cloud >
    Pro Edition"). Without them it is the nearest price line above (the tier heading), keyed by
    position, so a changed price doesn't make every line of the tier look moved."""
    out: list[str | None] = []
    current: str | None = None
    if any(ln.startswith(PATH_PREFIX) for ln in lines):
        for ln in lines:
            if ln.startswith(PATH_PREFIX):
                name = ln[len(PATH_PREFIX) :]
                current = f"{name}\t{name}"
            out.append(current)
        return out
    n = 0
    for ln in lines:
        if _is_price(ln, patterns):
            n += 1
            current = f"#{n}\t{ln}"
        out.append(current)
    return out


def _key(label: str | None) -> str | None:
    return label.split("\t", 1)[0] if label else None


def _name(label: str | None) -> str:
    return label.split("\t", 1)[-1] if label else ""


def diff_lines(
    a: list[str],
    b: list[str],
    context: int = 1,
    price_patterns: list[re.Pattern[str]] | None = None,
) -> LineDiff:
    """Line diff. A line that only moved is ignored, unless it moved from one priced section
    (the lines under a price line, i.e. a tier) to another: a feature changing tiers."""
    patterns = price_patterns or []
    sec_a, sec_b = _sections(a, patterns), _sections(b, patterns)

    ops = SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    rem = [
        (i, a[i]) for t, i1, i2, _, _ in ops if t in ("replace", "delete") for i in range(i1, i2)
    ]
    add = [
        (j, b[j]) for t, _, _, j1, j2 in ops if t in ("replace", "insert") for j in range(j1, j2)
    ]
    moved = Counter(ln for _, ln in rem) & Counter(ln for _, ln in add)

    # Moved from one section to another (Enterprise → Team): a feature changing tiers.
    tier_moves: list[TierMove] = []
    for ln in list(moved):
        if ln.startswith(PATH_PREFIX) or _is_price(ln, patterns):
            continue
        from_ = {_key(sec_a[i]) for i, x in rem if x == ln}
        to = {_key(sec_b[j]) for j, x in add if x == ln}
        if None not in from_ | to and from_ != to:
            i = next(i for i, x in rem if x == ln)
            j = next(j for j, x in add if x == ln)
            tier_moves.append(TierMove(ln, _name(sec_a[i]), _name(sec_b[j])))
            del moved[ln]

    def keep(lines: list[str], budget: Counter[str]) -> list[str]:
        out = []
        for ln in lines:
            if budget[ln] > 0:
                budget[ln] -= 1
            else:
                out.append(ln)
        return out

    hunks: list[Hunk] = []
    budget_r, budget_a = moved.copy(), moved.copy()
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        before, after = keep(a[i1:i2], budget_r), keep(b[j1:j2], budget_a)
        if not before and not after:
            continue
        ctx_before = b[max(0, j1 - context) : j1] if context else []
        # Which section the change is in: its path line, unless already visible.
        where = _path_before(b, j1) if after else _path_before(a, i1)
        if context and where and where not in ctx_before and where not in before + after:
            ctx_before = [where, *ctx_before]
        hunks.append(
            Hunk(
                before,
                after,
                context_before=ctx_before,
                context_after=b[j2 : j2 + context] if context else [],
            )
        )
    changed = [ln for h in hunks for ln in (*h.before, *h.after)]
    return LineDiff(
        hunks=hunks,
        removed=sum(len(h.before) for h in hunks),
        added=sum(len(h.after) for h in hunks),
        moved=sum(moved.values()),
        tier_moves=tier_moves,
        changed_chars=sum(_edit_size(h.before, h.after) for h in hunks),
        total_lines=len(a) + len(b),
        lines_before=len(a),
        lines_after=len(b),
        touches_prices=any(_is_price(ln, patterns) for ln in changed),
    )


def price_lines(lines: list[str], patterns: list[re.Pattern[str]]) -> list[str]:
    """Lines stating a price or plan limit, prefixed with "[section path] " when known."""
    out, current = [], ""
    for ln in lines:
        if ln.startswith(PATH_PREFIX):
            current = ln[len(PATH_PREFIX) :]
        elif _is_price(ln, patterns):
            out.append(f"[{current}] {ln}" if current else ln)
    return out


# ---------- classification ----------


@dataclass
class PairResult:
    kind: str  # no_change / noise / changed / restructured
    diff: LineDiff | None = None  # the diff the signal is built from (None = no signal)
    diff_mode: str | None = None  # full / price_lines
    full_stats: dict[str, Any] = field(default_factory=dict)

    @property
    def creates_signal(self) -> bool:
        return self.diff is not None


def _significant(d: LineDiff, cfg: PageDiffSettings) -> bool:
    """Big enough to report. Any change to a price line counts ("$4" → "$5" is 2 characters)."""
    return bool(d.hunks) and (d.touches_prices or d.changed_chars >= cfg.min_changed_chars)


def classify(before: str, after: str, cfg: PageDiffSettings) -> PairResult:
    """Compare two normalized texts (one line per row)."""
    if before == after:
        return PairResult("no_change")
    patterns = [re.compile(p, re.IGNORECASE) for p in cfg.price_patterns]
    a, b = before.splitlines(), after.splitlines()
    full = diff_lines(a, b, cfg.context_lines, patterns)
    stats = full.stats()
    if not _significant(full, cfg):
        return PairResult("noise", full_stats=stats)
    if full.changed_ratio <= cfg.restructure_ratio:
        return PairResult("changed", full, "full", stats)

    # Redesign: a full diff would be mostly layout churn. Compare only the price lines, each
    # prefixed with its section path ("[Nexus Repository > Cloud > Pro Edition] Starting at …").
    pa, pb = price_lines(a, patterns), price_lines(b, patterns)
    prices = diff_lines(pa, pb, context=0)
    stats["price_lines_before"], stats["price_lines_after"] = len(pa), len(pb)
    if prices.hunks:
        stats["price_diff"] = prices.stats()
        return PairResult("restructured", prices, "price_lines", stats)
    return PairResult("restructured", full_stats=stats)


# ---------- signal text ----------


@dataclass(frozen=True)
class SnapshotInfo:
    id: int | None
    captured_at: datetime
    origin: str  # live / wayback


def render_diff(
    source_name: str,
    url: str,
    before: SnapshotInfo,
    after: SnapshotInfo,
    result: PairResult,
    max_chars: int,
) -> tuple[str, bool]:
    """Readable before/after text of the changed sections only. Returns (text, truncated)."""
    assert result.diff is not None
    d = result.diff
    head = [
        f"Page: {source_name}",
        f"URL: {url}",
        f"Before: {before.captured_at.date().isoformat()} ({before.origin})",
        f"After: {after.captured_at.date().isoformat()} ({after.origin})",
        f"Changed lines: {d.removed} removed, {d.added} added",
        FORMAT_NOTE,
    ]
    if result.diff_mode == "price_lines":
        head.append(
            "Note: the page was redesigned; only lines stating prices or plan limits are compared."
        )
    parts = ["\n".join(head)]
    n = len(d.hunks)
    for i, h in enumerate(d.hunks, 1):
        block = [f"## Change {i} of {n}"]
        if h.context_before:
            block += [_CONTEXT, *h.context_before]
        block += [_BEFORE, *(h.before or ["(nothing)"])]
        block += [_AFTER, *(h.after or ["(removed)"])]
        if h.context_after:
            block += [_CONTEXT, *h.context_after]
        parts.append("\n".join(block))
    if d.tier_moves:
        moves = ["## Moved between sections (each line also appears under [after] above)"]
        moves += [
            f'- "{m.line}": from the section headed "{m.from_heading}" (old page) to the section '
            f'headed "{m.to_heading}" (new page)'
            for m in d.tier_moves
        ]
        parts.append("\n".join(moves))
    text = "\n\n".join(parts)
    if len(text) <= max_chars:
        return text, False
    cut = text[:max_chars].rsplit("\n## ", 1)[0]
    return cut + "\n\n[... further changes truncated]", True


def _section_text(raw_text: str, marker: str, placeholder: str) -> str:
    out: list[str] = []
    inside = False
    for line in raw_text.splitlines():
        if line in _MARKERS or line.startswith("## "):
            inside = line == marker
            continue
        if inside and line != placeholder:
            out.append(line)
    return "\n".join(out)


def after_text(raw_text: str) -> str:
    """Only the [after] lines of a rendered diff (where an evidence quote may come from)."""
    return _section_text(raw_text, _AFTER, "(removed)")


def before_text(raw_text: str) -> str:
    """Only the [before] lines (the old page)."""
    return _section_text(raw_text, _BEFORE, "(nothing)")


def quote_side(raw_text: str) -> str:
    """Where a diff's evidence quote comes from: "after" (the current page), or "before" when the
    change only removed lines, so there is no new text to quote (UI: "Removed: '…'")."""
    return "after" if after_text(raw_text).strip() else "before"


def quote_source(raw_text: str) -> tuple[str, str]:
    """(side, text) the evidence quote of a diff signal is checked against."""
    side = quote_side(raw_text)
    return side, after_text(raw_text) if side == "after" else before_text(raw_text)


def is_page_diff(meta: dict[str, Any] | None) -> bool:
    return bool(meta) and meta.get("kind") == "page_diff"
