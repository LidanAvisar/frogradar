"""Rule-based entity linking: which tracked companies/products a signal is about or mentions.

The LLM confirms/corrects these links during enrichment (S6); this step is cheap and has no LLM.

- Matches every approved entity's name and aliases on word boundaries in the title and raw_text.
- Per-term rules from entities.match_rules (competitors.yaml): case_sensitive, min_mentions,
  title_suffices, enabled. Default: case-insensitive, one mention anywhere.
- Overlapping matches: the longest wins (products before companies on a tie), so "GitHub Actions"
  counts for the product and not as a "GitHub" mention.
- A company is dropped when one of its products is linked, unless the company link says more
  (company in the title, product only in the text). The product's parent_id keeps the roll-up.
- is_primary = the entity is the source's entity or matched in the title; otherwise a mention.
- Evidence goes to meta.entity_links (term, where, mentions) together with a fingerprint of the
  text and the rules, so unchanged signals are skipped and re-runs change nothing.
- The linker replaces only links it made itself (listed in meta.entity_links); links from other
  stages (e.g. LLM enrichment) are left alone.
"""

import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.catalog import MatchRule
from app.db.enums import EntityReviewStatus, EntityType
from app.db.models import Entity, Signal, Source, signal_entities
from app.pipeline.context import RunContext
from app.pipeline.scope import in_scope

logger = logging.getLogger(__name__)

# Bump when the matching logic changes, so every signal is linked again.
LINKER_VERSION = 1


@dataclass(frozen=True)
class LinkEntity:
    id: int
    slug: str
    name: str
    type: EntityType
    parent_id: int | None
    aliases: tuple[str, ...]
    match_rules: dict[str, Any] = field(hash=False, compare=False)


@dataclass(frozen=True)
class Term:
    entity: LinkEntity
    text: str
    rule: MatchRule
    pattern: re.Pattern[str]


@dataclass
class Link:
    entity_id: int
    slug: str
    is_primary: bool
    term: str | None  # the name/alias that matched (None = source entity, no text match)
    where: str  # title / text / title+text / source
    mentions: int


def _pattern(term: str, case_sensitive: bool) -> re.Pattern[str]:
    # \w lookarounds instead of \b: terms may start/end with non-word chars ("GitLab CI/CD").
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", flags)


class Matcher:
    def __init__(self, entities: list[LinkEntity]) -> None:
        self.entities = {e.id: e for e in entities}
        self.terms: list[Term] = []
        for e in entities:
            for text in (e.name, *e.aliases):
                rule = MatchRule(**e.match_rules.get(text, {}))
                if rule.enabled:
                    self.terms.append(Term(e, text, rule, _pattern(text, rule.case_sensitive)))
        blob = json.dumps(
            [LINKER_VERSION]
            + [[e.id, e.name, list(e.aliases), e.match_rules, e.parent_id] for e in entities],
            sort_keys=True,
        )
        self.fingerprint = hashlib.sha256(blob.encode()).hexdigest()

    @classmethod
    def from_db(cls, session: Session) -> "Matcher":
        rows = session.scalars(
            select(Entity)
            .where(Entity.review_status == EntityReviewStatus.APPROVED)
            .order_by(Entity.id)
        ).all()
        return cls(
            [
                LinkEntity(
                    e.id,
                    e.slug,
                    e.name,
                    EntityType(e.type),
                    e.parent_id,
                    tuple(e.aliases),
                    dict(e.match_rules or {}),
                )
                for e in rows
            ]
        )

    def _hits(self, text: str) -> dict[Term, int]:
        """Count matches per term after resolving overlaps (longest first, products first)."""
        found = [(m.start(), m.end(), t) for t in self.terms for m in t.pattern.finditer(text)]
        found.sort(key=lambda h: (-(h[1] - h[0]), h[2].entity.type is not EntityType.PRODUCT, h[0]))
        taken: list[tuple[int, int]] = []
        counts: dict[Term, int] = defaultdict(int)
        for start, end, term in found:
            if any(start < e and s < end for s, e in taken):
                continue
            taken.append((start, end))
            counts[term] += 1
        return counts

    def match(self, title: str, body: str, source_entity_id: int | None) -> list[Link]:
        in_title, in_body = self._hits(title), self._hits(body)
        links: dict[int, Link] = {}
        for term in self.terms:
            t, b = in_title.get(term, 0), in_body.get(term, 0)
            if not (t or b):
                continue
            rule = term.rule
            if not ((t and rule.title_suffices) or t + b >= rule.min_mentions):
                continue
            where = "title+text" if t and b else "title" if t else "text"
            eid = term.entity.id
            prev = links.get(eid)
            if prev is None:
                links[eid] = Link(eid, term.entity.slug, bool(t), term.text, where, t + b)
                continue
            # Several terms of one entity matched: keep the strongest as evidence, add mentions.
            prev.mentions += t + b
            if t and not prev.is_primary:
                prev.is_primary, prev.term, prev.where = True, term.text, where

        self._prefer_products(links, source_entity_id)

        if source_entity_id is not None and source_entity_id in self.entities:
            src = links.get(source_entity_id)
            if src is None:
                e = self.entities[source_entity_id]
                links[source_entity_id] = Link(e.id, e.slug, True, None, "source", 0)
            else:
                src.is_primary = True
        return sorted(links.values(), key=lambda link: (not link.is_primary, link.slug))

    def _prefer_products(self, links: dict[int, Link], source_entity_id: int | None) -> None:
        products_by_company: dict[int, list[Link]] = defaultdict(list)
        for link in links.values():
            parent = self.entities[link.entity_id].parent_id
            if parent is not None:
                products_by_company[parent].append(link)
        for company_id, products in products_by_company.items():
            company = links.get(company_id)
            if company is None or company_id == source_entity_id:
                continue
            if company.is_primary and not any(p.is_primary for p in products):
                continue  # company in the title, products only in the text: keep both
            del links[company_id]


def signal_fingerprint(matcher: Matcher, content_hash: str, source_entity_id: int | None) -> str:
    raw = f"{matcher.fingerprint}:{content_hash}:{source_entity_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


def link_signals(session: Session, ctx: RunContext) -> dict[str, Any]:
    matcher = Matcher.from_db(session)
    rows = session.execute(
        select(
            Signal.id,
            Signal.title,
            Signal.raw_text,
            Signal.content_hash,
            Signal.meta,
            Source.entity_id.label("source_entity_id"),
        )
        .select_from(Signal)
        .outerjoin(Source, Source.id == Signal.source_id)
        .where(in_scope())
        .order_by(Signal.id)
    ).all()
    counts = {
        "checked": len(rows),
        "unchanged": 0,
        "linked": 0,
        "links_added": 0,
        "links_removed": 0,
        "primary": 0,
        "mentions": 0,
    }

    for r in rows:
        meta = dict(r.meta or {})
        fp = signal_fingerprint(matcher, r.content_hash, r.source_entity_id)
        old = meta.get("entity_links") or {}
        if old.get("fingerprint") == fp:
            counts["unchanged"] += 1
            continue
        links = matcher.match(r.title, r.raw_text, r.source_entity_id)
        new_ids = {link.entity_id for link in links}
        existing = set(
            session.scalars(
                select(signal_entities.c.entity_id).where(signal_entities.c.signal_id == r.id)
            )
        )
        stale = {link["entity_id"] for link in old.get("links", [])} - new_ids
        if stale:
            session.execute(
                delete(signal_entities).where(
                    signal_entities.c.signal_id == r.id, signal_entities.c.entity_id.in_(stale)
                )
            )
        if links:
            stmt = insert(signal_entities).values(
                [
                    {"signal_id": r.id, "entity_id": link.entity_id, "is_primary": link.is_primary}
                    for link in links
                ]
            )
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["signal_id", "entity_id"],
                    set_={"is_primary": stmt.excluded.is_primary},
                )
            )
        meta["entity_links"] = {"fingerprint": fp, "links": [asdict(link) for link in links]}
        session.execute(update(Signal).where(Signal.id == r.id).values(meta=meta))

        counts["linked"] += 1
        counts["links_added"] += len(new_ids - existing)
        counts["links_removed"] += len(stale & existing)
        counts["primary"] += sum(link.is_primary for link in links)
        counts["mentions"] += sum(not link.is_primary for link in links)
    session.flush()
    logger.info("link: %s", counts)
    return counts
