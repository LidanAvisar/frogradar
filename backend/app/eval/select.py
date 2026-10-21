"""Stratified eval-set selection from the full catalog (no LLM calls).

40 signals, fixed seed, so the set is reproducible from the same DB:
- hard cases: #50, #77, #1005, #1053, #1055 (known difficult items from earlier runs);
- one more pricing diff (GitHub Pricing), so 3 page diffs in total;
- in scope (demo sources): one triage-relevant item per stored category, one extra
  feature_update, and 5 triage-irrelevant items;
- out of scope (inactive sources, never processed): one item per category guessed from the title
  (keywords), GitHub releases (2 minor/major, 2 patch/pre-release), 3 press items without a category
  keyword (expected irrelevant), the rest random official posts. At most 2 per source.

Stored pipeline outputs only steer which items are picked; they are never shown to the silver
labeler. The stratum column says why each item was picked.
"""

import random
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Signal, Source
from app.eval.files import LabelRow
from app.pipeline.page_diff import is_page_diff
from app.pipeline.scope import full_catalog, in_scope

SEED = 8
SIZE = 40
HARD_CASES = (50, 77, 1005, 1053, 1055)
MAX_PER_SOURCE = 2  # out-of-scope picks per source

# Title keywords → a guess of the category, only to spread out-of-scope picks across categories.
CATEGORY_HINTS: dict[str, str] = {
    "acquisition": r"\bacquir|\bacquisition",
    "funding": r"\braises?\b|\bfunding\b|\bseries [a-e]\b",
    "leadership_change": r"\bappoint|\bnew (?:ceo|cto|cfo)\b|\bnames .* (?:ceo|cto|chief)\b",
    "customer_win": r"\bcase study\b|\bcustomer story\b|\bhow .+ (?:uses|scaled|secures)\b",
    "event": r"\bwebinar|\bconference\b|\bsummit\b|\bkubecon\b|\bjoin us\b",
    "pricing_change": r"\bpricing\b|\bprice",
    "security_incident": r"\bmalicious|\bcompromis|\bbreach|\bhijack|\bbackdoor|\bmalware|\bworm\b",
    "partnership": r"\bpartner",
    "analyst_report": r"\bgartner|\bforrester|\bmagic quadrant\b|\breport\b|\bsurvey\b",
    "product_launch": r"\bintroduc|\blaunch|\bgenerally available\b|\bnow available\b",
    "open_source": r"\bopen[- ]source",
}
_PATCH = re.compile(r"v?\d+\.\d+\.(?:[1-9]\d*)\b|-(?:rc|beta|alpha)|\bpre-?release")


@dataclass(frozen=True)
class Candidate:
    id: int
    title: str
    source: str
    kind: str  # source kind: official / press / github
    url: str
    date: str
    in_scope: bool
    page_diff: bool
    prerelease: bool
    relevant: bool | None  # stored triage decision (selection only)
    category: str | None  # stored category (selection only)


def load_candidates(session: Session) -> list[Candidate]:
    scoped = set(session.scalars(select(Signal.id).where(in_scope())))
    rows = session.execute(
        select(
            Signal.id,
            Signal.title,
            Signal.url,
            Signal.published_at,
            Signal.meta,
            Signal.relevant,
            Signal.category,
            Source.name,
            Source.kind,
        )
        .outerjoin(Source, Source.id == Signal.source_id)
        .where(full_catalog())
        .order_by(Signal.id)
    ).all()
    return [
        Candidate(
            id=r.id,
            title=r.title,
            source=r.name or "research finding",
            kind=str(r.kind or "none"),
            url=r.url,
            date=r.published_at.date().isoformat() if r.published_at else "",
            in_scope=r.id in scoped,
            page_diff=is_page_diff(r.meta),
            prerelease=bool((r.meta or {}).get("prerelease")),
            relevant=r.relevant,
            category=str(r.category) if r.category else None,
        )
        for r in rows
    ]


def category_hint(title: str) -> str | None:
    for category, pattern in CATEGORY_HINTS.items():
        if re.search(pattern, title, re.IGNORECASE):
            return category
    return None


def is_patch_release(c: Candidate) -> bool:
    return c.prerelease or bool(_PATCH.search(c.title))


def select_eval_set(candidates: list[Candidate], *, seed: int = SEED, size: int = SIZE):
    """[(candidate, stratum)] – deterministic for the same candidates and seed."""
    rng = random.Random(seed)
    by_id = {c.id: c for c in candidates}
    picked: dict[int, str] = {}
    per_source: Counter[str] = Counter()

    def take(c: Candidate, stratum: str) -> None:
        picked[c.id] = stratum
        per_source[c.source] += 1

    def pick(pool: list[Candidate], stratum: str, n: int = 1, cap: bool = False) -> None:
        pool = [c for c in pool if c.id not in picked]
        rng.shuffle(pool)
        for c in pool:
            if n == 0:
                return
            if cap and per_source[c.source] >= MAX_PER_SOURCE:
                continue
            take(c, stratum)
            n -= 1

    def where(pred: Callable[[Candidate], bool]) -> list[Candidate]:
        return sorted((c for c in candidates if pred(c)), key=lambda c: c.id)

    for i in HARD_CASES:
        if i in by_id:
            take(by_id[i], "hard_case" + (":page_diff" if by_id[i].page_diff else ""))
    pick(where(lambda c: c.page_diff and c.source == "GitHub Pricing"), "page_diff")

    # In scope (stored triage/category steer the spread only).
    scoped = [c for c in candidates if c.in_scope and not c.page_diff]
    for cat in sorted({c.category for c in scoped if c.relevant and c.category}):
        pick([c for c in scoped if c.relevant and c.category == cat], f"in_scope:relevant:{cat}")
    pick(
        [c for c in scoped if c.relevant and c.category == "feature_update"],
        "in_scope:relevant:feature_update",
    )
    pick([c for c in scoped if c.relevant is False], "in_scope:irrelevant", n=5)

    # Out of scope: never triaged; spread by title keywords and source kind.
    out = [c for c in candidates if not c.in_scope and not c.page_diff]
    articles = [c for c in out if c.kind in ("official", "press")]
    for cat in CATEGORY_HINTS:
        pool = [c for c in articles if category_hint(c.title) == cat]
        pick(pool, f"out_of_scope:hint:{cat}", cap=True)
    releases = [c for c in out if c.kind == "github"]
    pick([c for c in releases if not is_patch_release(c)], "out_of_scope:release", 2, cap=True)
    pick([c for c in releases if is_patch_release(c)], "out_of_scope:patch_release", 2, cap=True)
    press = [c for c in articles if c.kind == "press" and category_hint(c.title) is None]
    pick(press, "out_of_scope:press_no_hint", 3, cap=True)
    official = [c for c in articles if c.kind == "official"]
    pick(official, "out_of_scope:official", size - len(picked), cap=True)

    return [(by_id[i], s) for i, s in sorted(picked.items())][:size]


def to_rows(selection) -> list[LabelRow]:
    return [
        LabelRow(
            id=c.id,
            title=c.title,
            source=c.source,
            date=c.date,
            url=c.url,
            in_scope="y" if c.in_scope else "n",
            stratum=stratum,
        )
        for c, stratum in selection
    ]


def summary(rows: list[LabelRow]) -> dict[str, int]:
    """Counts per stratum group, for the CLI and the report."""
    counts: Counter[str] = Counter()
    for r in rows:
        counts["in_scope" if r.in_scope == "y" else "out_of_scope"] += 1
        counts[r.stratum.split(":")[0] if r.stratum.startswith("hard") else r.stratum] += 1
    return dict(sorted(counts.items()))
