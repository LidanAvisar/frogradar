"""Upsert entities, sources and capabilities from config/ into the DB. Safe to run repeatedly.

Usage: python -m app.db.seed   (or `make seed`)

- Config owns: names, aliases, match rules, segments, relationship, parent, source settings,
  capability text.
- The DB owns (set on insert, never overwritten): review_status, is_tracked, discovered_by,
  last_fetched_at.
- Config sources that disappear from config are deactivated, never deleted (signals reference them).
  Sources come from competitors.yaml, sources.yaml and discovery.yaml (Competitor Radar).
- Entities and sources created by the radar (discovered_by radar, analyst approval) are never
  touched: seed only upserts config slugs / urls and only deactivates discovered_by config sources.
"""

import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Table, func, literal_column, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.catalog import (
    CompanyConfig,
    ConfigError,
    ProductConfig,
    SourceConfig,
    load_capabilities,
    load_competitors,
    load_discovery,
    load_self_capabilities,
    load_sources,
)
from app.db.checks import assert_embedding_dim_matches_db
from app.db.enums import CapabilityLevel, DiscoveredBy, EntityReviewStatus, EntityType, Relationship
from app.db.models import Capability, CapabilityMatrix, Entity, Source


@dataclass
class TableCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    deactivated: int = 0


@dataclass
class SeedReport:
    tables: dict[str, TableCounts] = field(default_factory=dict)

    def __str__(self) -> str:
        return "\n".join(
            f"{name:<17} inserted={c.inserted} updated={c.updated} "
            f"unchanged={c.unchanged} deactivated={c.deactivated}"
            for name, c in self.tables.items()
        )


def _upsert(
    session: Session,
    table: Table,
    rows: Sequence[dict[str, Any]],
    key: str,
    update_cols: Sequence[str],
    counts: TableCounts,
) -> dict[str, int]:
    """INSERT ... ON CONFLICT (key) DO UPDATE only when a config-owned column changed.

    Returns {key value: id} for all rows.
    """
    if not rows:
        return {}
    stmt = insert(table).values(list(rows))
    changed = tuple_(*(table.c[c] for c in update_cols)).is_distinct_from(
        tuple_(*(stmt.excluded[c] for c in update_cols))
    )
    set_ = {c: stmt.excluded[c] for c in update_cols}
    if "updated_at" in table.c:
        set_["updated_at"] = func.now()
    # Postgres sets xmax = 0 only on rows this statement inserted (not on updated ones).
    stmt = stmt.on_conflict_do_update(index_elements=[key], set_=set_, where=changed).returning(
        table.c[key], literal_column("xmax = 0").label("inserted")
    )
    touched = session.execute(stmt).all()
    inserted = sum(1 for _, is_new in touched if is_new)
    counts.inserted += inserted
    counts.updated += len(touched) - inserted
    counts.unchanged += len(rows) - len(touched)

    keys = [r[key] for r in rows]
    return dict(
        session.execute(select(table.c[key], table.c.id).where(table.c[key].in_(keys))).all()
    )


def _entity_row(
    item: ProductConfig, type_: EntityType, relationship: Relationship, parent_id: int | None
) -> dict[str, Any]:
    return {
        "type": type_,
        "name": item.name,
        "slug": item.slug,
        "aliases": item.alias_names,
        "match_rules": item.match_rules,
        "segments": [s.value for s in item.segments],
        "website": item.website,
        "description": item.description,
        "discontinued_on": item.discontinued.end_of_life
        if getattr(item, "discontinued", None)
        else None,
        "relationship": relationship,
        # Only the JFrog company row is "self"; its products keep relationship=self.
        "is_self": type_ is EntityType.COMPANY and relationship is Relationship.SELF,
        "parent_id": parent_id,
        # Insert-only fields (not in ENTITY_UPDATE_COLS):
        "is_tracked": item.is_tracked if isinstance(item, CompanyConfig) else True,
        "review_status": EntityReviewStatus.APPROVED,
        "discovered_by": DiscoveredBy.CONFIG,
    }


ENTITY_UPDATE_COLS = (
    "type", "name", "aliases", "match_rules", "segments", "website", "description",
    "discontinued_on", "relationship", "is_self", "parent_id",
)  # fmt: skip
SOURCE_UPDATE_COLS = (
    "entity_id",
    "name",
    "kind",
    "fetch_method",
    "trust_score",
    "options",
    "is_active",
)
CAPABILITY_UPDATE_COLS = ("name", "segment", "description")


def _source_row(src: SourceConfig, entity_id: int | None) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "name": src.name,
        "url": src.url,
        "kind": src.kind,
        "fetch_method": src.fetch_method,
        "trust_score": src.effective_trust,
        "options": dict(src.options),
        "is_active": src.is_active,
        "discovered_by": DiscoveredBy.CONFIG,
    }


def seed(session: Session, config_dir: Path | None = None) -> SeedReport:
    """Upsert everything from config in the caller's transaction (caller commits)."""
    competitors = load_competitors(config_dir)
    industry = load_sources(config_dir)
    discovery = load_discovery(config_dir)
    capabilities = load_capabilities(config_dir)

    files = {
        "competitors.yaml": {s.url for s in competitors.all_sources()},
        "sources.yaml": {s.url for s in industry.sources},
        "discovery.yaml": {s.url for s in discovery.sources},
    }
    names = sorted(files)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            if overlap := files[a] & files[b]:
                raise ConfigError(f"source urls in both {a} and {b}: {sorted(overlap)}")

    report = SeedReport({t: TableCounts() for t in ("entities", "sources", "capabilities")})
    entities_t, sources_t = Entity.__table__, Source.__table__

    # 1. Companies, then products (they need their parent's id).
    company_ids = _upsert(
        session,
        entities_t,
        [_entity_row(c, EntityType.COMPANY, c.relationship, None) for c in competitors.companies],
        "slug",
        ENTITY_UPDATE_COLS,
        report.tables["entities"],
    )
    product_rows = [
        _entity_row(p, EntityType.PRODUCT, c.relationship, company_ids[c.slug])
        for c in competitors.companies
        for p in c.products
    ]
    product_ids = _upsert(
        session, entities_t, product_rows, "slug", ENTITY_UPDATE_COLS, report.tables["entities"]
    )
    entity_ids = company_ids | product_ids

    # 2. Sources: competitor sources point at their company/product, industry sources at nothing.
    source_rows = [
        _source_row(s, entity_ids[owner.slug])
        for c in competitors.companies
        for owner in (c, *c.products)
        for s in owner.sources
    ] + [_source_row(s, None) for s in (*industry.sources, *discovery.sources)]
    _upsert(session, sources_t, source_rows, "url", SOURCE_UPDATE_COLS, report.tables["sources"])

    config_urls = [r["url"] for r in source_rows]
    result = session.execute(
        update(sources_t)
        .where(
            sources_t.c.discovered_by == DiscoveredBy.CONFIG,
            sources_t.c.is_active.is_(True),
            sources_t.c.url.not_in(config_urls),
        )
        .values(is_active=False, updated_at=func.now())
    )
    report.tables["sources"].deactivated = result.rowcount

    # 3. Capability taxonomy.
    cap_rows = [
        {"slug": cap.slug, "name": cap.name, "segment": segment, "description": cap.description}
        for segment, caps in capabilities.segments.items()
        for cap in caps
    ]
    _upsert(
        session,
        Capability.__table__,
        cap_rows,
        "slug",
        CAPABILITY_UPDATE_COLS,
        report.tables["capabilities"],
    )

    # 4. JFrog's own capability levels (config/jfrog_capabilities.yaml, not the LLM).
    report.tables["capability_matrix"] = _seed_self_levels(session, config_dir)
    return report


def _seed_self_levels(session: Session, config_dir: Path | None) -> TableCounts:
    """Upsert one capability_matrix row per capability for the self entity (no evidence signal;
    the note carries the doc). Level changes record previous_level / level_changed_at."""
    counts = TableCounts()
    levels = load_self_capabilities(config_dir)
    self_id = session.scalar(select(Entity.id).where(Entity.is_self))
    if self_id is None:
        return counts
    cap_ids = dict(session.execute(select(Capability.slug, Capability.id)).all())
    existing = {
        row.capability_id: row
        for row in session.scalars(
            select(CapabilityMatrix).where(CapabilityMatrix.entity_id == self_id)
        )
    }
    for slug, cfg in levels.capabilities.items():
        notes = f"{cfg.note} Source: {cfg.source}" if cfg.source else cfg.note
        level = CapabilityLevel(cfg.level)
        row = existing.get(cap_ids[slug])
        if row is None:
            session.add(
                CapabilityMatrix(
                    entity_id=self_id, capability_id=cap_ids[slug], level=level, notes=notes
                )
            )
            counts.inserted += 1
        elif row.level != level or row.notes != notes:
            if row.level != level:
                row.previous_level, row.level_changed_at = row.level, func.now()
            row.level, row.notes = level, notes
            counts.updated += 1
        else:
            counts.unchanged += 1
    session.flush()
    return counts


def main() -> int:
    from app.db.session import get_engine

    engine = get_engine()
    try:
        with engine.begin() as conn:
            assert_embedding_dim_matches_db(conn)
        with Session(engine) as session, session.begin():
            report = seed(session)
    except Exception as e:  # noqa: BLE001 - CLI: print a clean error, non-zero exit
        print(f"seed failed: {getattr(e, 'orig', None) or e}", file=sys.stderr)
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
