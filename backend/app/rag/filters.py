"""Structured filters inferred from the question in code (D65), never left to the model.

company: a tracked company, product (→ its company) or alias named with word boundaries. Exactly
         one company must be named; two different companies ("GitHub vs Sonatype") → no filter.
         JFrog itself never becomes a filter (every question is about JFrog's market).
window:  "today / last 24 hours" → 1 day, "this week / last 7 days" → 7 days, "this month / last 30
         days" → 30 days, "this quarter / last 90 days" → 90 days, "this year" → since 1 January,
         "in October [2026]" → that calendar month (a month later than now → the previous year).
"""

import calendar
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import EntityReviewStatus
from app.db.models import Entity


@dataclass(frozen=True)
class Filters:
    company_slug: str | None = None
    company_name: str | None = None
    entity_ids: tuple[int, ...] = ()  # the company and its products
    since: datetime | None = None
    until: datetime | None = None
    window: str | None = None  # human label: "last 7 days", "October 2026"
    # Signal categories the question names ("incidents" → security_incident). Not a filter: a third
    # ranked list in the fusion, so signals of that category rank higher.
    categories: tuple[str, ...] = ()
    # Battlecard sections the question names ("weaknesses" → weaknesses): another ranked list.
    sections: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return self.company_slug is None and self.since is None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["entity_ids"] = list(self.entity_ids)
        d["categories"] = list(self.categories)
        d["sections"] = list(self.sections)
        d["since"] = self.since.isoformat() if self.since else None
        d["until"] = self.until.isoformat() if self.until else None
        return d


@dataclass(frozen=True)
class Name:
    term: str
    company_id: int


def entity_names(session: Session) -> tuple[list[Name], dict[int, Entity], dict[int, list[int]]]:
    """(names longest first, companies by id, company id → its entity ids) of approved,
    non-JFrog entities."""
    rows = session.scalars(
        select(Entity).where(Entity.review_status == EntityReviewStatus.APPROVED)
    ).all()
    by_id = {e.id: e for e in rows}
    companies = {e.id: e for e in rows if e.parent_id is None and not e.is_self}
    family: dict[int, list[int]] = {cid: [cid] for cid in companies}
    names: list[Name] = []
    for e in rows:
        top = e.parent_id if e.parent_id in companies else e.id
        if top not in companies or (e.parent_id and by_id.get(e.parent_id, e).is_self):
            continue
        if e.id != top:
            family[top].append(e.id)
        for term in {e.name, *(e.aliases or [])}:
            if term and len(term) >= 3:
                names.append(Name(term, top))
    names.sort(key=lambda n: -len(n.term))
    return names, companies, family


def _company(question: str, names: list[Name]) -> int | None:
    found: set[int] = set()
    taken: list[tuple[int, int]] = []
    for n in names:
        for m in re.finditer(rf"(?<![\w-]){re.escape(n.term)}(?![\w-])", question, re.IGNORECASE):
            span = m.span()
            if any(a < span[1] and span[0] < b for a, b in taken):
                continue  # inside a longer name already matched
            taken.append(span)
            found.add(n.company_id)
    return found.pop() if len(found) == 1 else None


MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_I = re.IGNORECASE
WINDOWS: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(r"\b(today|(last|past) 24 hours|(last|past) day)\b", _I), 1, "last 24 hours"),
    (re.compile(r"\b((this|last|past) week|(last|past) (7|seven) days)\b", _I), 7, "last 7 days"),
    (re.compile(r"\b((this|last|past) month|(last|past) 30 days)\b", _I), 30, "last 30 days"),
    (re.compile(r"\b((this|last|past) quarter|(last|past) 90 days)\b", _I), 90, "last 90 days"),
]
MONTH_RE = re.compile(
    rf"\b(?:in|during|since)?\s*({'|'.join(MONTHS)})(?:\s+(\d{{4}}))?\b", re.IGNORECASE
)
YEAR_RE = re.compile(r"\bthis year\b", re.IGNORECASE)


def _window(question: str, now: datetime) -> tuple[datetime | None, datetime | None, str | None]:
    for pattern, days, label in WINDOWS:
        if pattern.search(question):
            return now - timedelta(days=days), None, label
    m = MONTH_RE.search(question)
    if m and (m.group(0).strip().lower().startswith(("in ", "during ", "since ")) or m.group(2)):
        month = MONTHS[m.group(1).lower()]
        year = int(m.group(2)) if m.group(2) else now.year
        if not m.group(2) and month > now.month:
            year -= 1
        start = datetime(year, month, 1, tzinfo=UTC)
        end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=UTC)
        if m.group(0).strip().lower().startswith("since "):
            return start, None, f"since {calendar.month_name[month]} {year}"
        return start, end, f"{calendar.month_name[month]} {year}"
    if YEAR_RE.search(question):
        return datetime(now.year, 1, 1, tzinfo=UTC), None, f"{now.year}"
    return None, None, None


CATEGORY_WORDS: dict[str, re.Pattern[str]] = {
    "security_incident": re.compile(
        r"\b(incidents?|breach\w*|attacks?|hijack\w*|compromis\w*|malicious|exploit\w*)\b", _I
    ),
    "pricing_change": re.compile(r"\b(pric\w*|costs?|charges?|tiers?|plans?|cheaper)\b", _I),
    "product_launch": re.compile(r"\b(launch\w*|released?|ships?|shipped|introduc\w*)\b", _I),
    "acquisition": re.compile(r"\b(acqui\w*|bought|buys)\b", _I),
    "funding": re.compile(r"\b(fund\w*|raised?|investment|valuation)\b", _I),
    "partnership": re.compile(r"\b(partner\w*|alliance)\b", _I),
    "analyst_report": re.compile(r"\b(reports?|survey|index|study|research)\b", _I),
    "leadership_change": re.compile(r"\b(ceo|cto|executive|hired?|appoint\w*)\b", _I),
}


SECTION_WORDS: dict[str, re.Pattern[str]] = {
    "strengths": re.compile(r"\b(strengths?|strong|advantages?)\b", _I),
    "weaknesses": re.compile(r"\b(weakness\w*|weak|gaps?|disadvantages?)\b", _I),
    "opportunities_for_jfrog": re.compile(r"\b(opportunit\w*|win against|beat)\b", _I),
    "blind_spots_for_jfrog": re.compile(r"\b(blind spots?|risks? for jfrog)\b", _I),
    "positioning": re.compile(r"\b(position\w*)\b", _I),
}


def infer_sections(question: str) -> tuple[str, ...]:
    return tuple(s for s, p in SECTION_WORDS.items() if p.search(question))


def infer_categories(question: str) -> tuple[str, ...]:
    """Categories named in the question; more than two → none (too broad to help ranking)."""
    found = tuple(c for c, p in CATEGORY_WORDS.items() if p.search(question))
    return found if len(found) <= 2 else ()


def infer_filters(session: Session, question: str, now: datetime) -> Filters:
    names, companies, family = entity_names(session)
    cid = _company(question, names)
    since, until, label = _window(question, now)
    company = companies.get(cid) if cid is not None else None
    return Filters(
        company_slug=company.slug if company else None,
        company_name=company.name if company else None,
        entity_ids=tuple(sorted(family.get(cid, []))) if company else (),
        since=since,
        until=until,
        window=label,
        categories=infer_categories(question),
        sections=infer_sections(question),
    )
