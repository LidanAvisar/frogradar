"""Enrich: relevant canonical signals get category, summary, why JFrog cares, impact, evidence
quote, is_claim, entity links, new-entity candidates and capabilities (bulk model).

- Selection: in scope, canonical (or not clustered), relevant, and not yet enriched with the
  current prompt version (or everything with --force enrich).
- Evidence quote: checked in code against raw_text (app.pipeline.quotes). A near-miss is first
  repaired to the real source span (one sentence or two adjacent ones, similarity and coverage
  >= quote_repair_threshold; meta.quote_repaired, D39). Otherwise the model gets the error and
  tries again (quote_retries); still failing → stored with evidence_quote_verified=false
  (confidence is capped; the UI must not show it as a quote).
- Page-diff signals (meta.kind = page_diff) use the enrich_page_diff prompt, and their quote must
  come from the [after] text of the diff, or from [before] when the change only removed lines
  (meta.quote_side = "before"; the UI shows it as "Removed: '…'") (D40).
- Entities: the model confirms/corrects the rule-based links (slugs validated by the schema).
  Final links = the model's list + the source's own entity (always primary). Rule links the model
  didn't confirm are removed; meta.entity_review records confirmed / added / dropped.
- Re-enrichment resets verification to pending, so the new text is verified again.
- Duplicates inherit everything from their canonical (inherit_from_canonicals).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, case, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from app.core.catalog import EnrichSettings
from app.core.llm import load_prompt
from app.core.llm.structured import retry_messages
from app.db.enums import Verification
from app.db.models import Capability, Entity, Signal, Source, signal_capabilities, signal_entities
from app.pipeline.context import CompleteFn, RunContext
from app.pipeline.llm_steps import canonical_or_unclustered, load_input, merge_meta, run_parallel
from app.pipeline.page_diff import is_page_diff, split_section
from app.pipeline.page_diff import quote_source as page_quote_source
from app.pipeline.price_verify import verify_price_changes
from app.pipeline.prompting import Catalog, SignalInput, enrich_messages
from app.pipeline.quotes import quote_error, quote_in_text, repair_quote
from app.pipeline.schemas import (
    EnrichedPageDiffBase,
    EnrichedSignalBase,
    EnrichSchemas,
    enrich_schemas,
)
from app.pipeline.scope import discovery, in_scope

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnrichOutcome:
    """Result of enrich_one: the (possibly quote-repaired) output and how the quote check went."""

    result: EnrichedSignalBase
    quote_ok: bool  # evidence_quote is verbatim in the allowed source text
    attempts: int  # model calls (1 + quote retries)
    repair: dict[str, Any] | None  # D39 code repair: original quote, similarity, coverage
    quote_side: str | None  # page diffs: "after" / "before"
    quote_section: str | None  # page diffs: section path split off the quote
    prompt_version: str


def enrich_one(
    sig: SignalInput,
    meta: dict[str, Any] | None,
    catalog: Catalog,
    schemas: EnrichSchemas,
    cfg: EnrichSettings,
    complete: CompleteFn,
) -> EnrichOutcome:
    """Enrich one signal incl. the quote check / repair / retry (no DB writes).
    Shared by the stage and the eval runner (app.eval)."""
    diff = is_page_diff(meta)
    disc = not diff and is_discovery(meta)
    schema = schemas.pick(diff, disc)
    prompt_name = DIFF_PROMPT if diff else DISCOVERY_PROMPT if disc else "enrich_signal"
    # Where the quote may come from: the whole text, or only the [after] lines of a diff.
    # Removal-only diffs have no [after] text: their quote comes from [before] (quote_side).
    side, quote_source = page_quote_source(sig.raw_text) if diff else (None, sig.raw_text)
    prompt, messages = enrich_messages(sig, catalog, cfg.max_chars, prompt_name)
    result = complete("bulk", prompt_name, messages, schema, prompt_version=prompt.id)
    attempts = 1
    repair: dict[str, Any] | None = None
    quote_ok = quote_in_text(result.evidence_quote, quote_source)
    while not quote_ok:
        fixed = repair_quote(result.evidence_quote, quote_source, cfg.quote_repair_threshold)
        if fixed is not None:
            repair = {
                "original": result.evidence_quote,
                "similarity": round(fixed.similarity, 3),
                "coverage": round(fixed.coverage, 3),
            }
            result = result.model_copy(update={"evidence_quote": fixed.span})
            quote_ok = True
            break
        if attempts > cfg.quote_retries:
            break
        error = quote_error(result.evidence_quote, diff_side=side)
        retry = [*messages, *retry_messages(result.model_dump_json(), error)]
        result = complete("bulk", prompt_name, retry, schema, prompt_version=prompt.id)
        attempts += 1
        quote_ok = quote_in_text(result.evidence_quote, quote_source)

    # Price-lines diffs prefix lines with "[section path] ": keep the page text as the quote
    # and the section next to it (meta.quote_section).
    section, page_quote = split_section(result.evidence_quote) if diff else (None, "")
    if section is not None and quote_in_text(page_quote, quote_source):
        result = result.model_copy(update={"evidence_quote": page_quote})
    return EnrichOutcome(result, quote_ok, attempts, repair, side, section, prompt.id)


def enrich_signals(session: Session, ctx: RunContext) -> dict[str, Any]:
    complete, factory = ctx.require_llm()
    version = load_prompt("enrich_signal").id
    diff_version = load_prompt(DIFF_PROMPT).id
    disc_version = load_prompt(DISCOVERY_PROMPT).id
    cfg = ctx.config.enrich
    todo = [in_scope(), canonical_or_unclustered(), Signal.relevant.is_(True)]
    if "enrich" not in ctx.force:
        # Each kind of signal is compared with its own prompt's current version.
        current = case((_page_diff_sql(), diff_version), (discovery(), disc_version), else_=version)
        todo.append(
            or_(
                Signal.enriched_at.is_(None), Signal.enrich_prompt_version.is_distinct_from(current)
            )
        )
    ids = session.scalars(select(Signal.id).where(*todo).order_by(Signal.id)).all()
    catalog = Catalog.load(session)
    schemas = enrich_schemas(catalog.entity_slugs, catalog.capability_slugs)
    entity_ids = dict(session.execute(select(Entity.slug, Entity.id)).all())
    tracked = tracked_terms(session)
    capability_ids = dict(session.execute(select(Capability.slug, Capability.id)).all())

    def worker(signal_id: int) -> str:
        with factory() as s:
            sig = load_input(s, signal_id)
            source_entity_id, meta = s.execute(
                select(Source.entity_id, Signal.meta)
                .select_from(Signal)
                .outerjoin(Source, Source.id == Signal.source_id)
                .where(Signal.id == signal_id)
            ).one()
        out = enrich_one(sig, meta, catalog, schemas, cfg, complete)
        result, quote_ok, attempts, repair = out.result, out.quote_ok, out.attempts, out.repair
        side, section, prompt_id = out.quote_side, out.quote_section, out.prompt_version

        with factory.begin() as s:
            review = apply_entity_links(
                s, signal_id, result, entity_ids, source_entity_id, meta or {}
            )
            s.execute(
                delete(signal_capabilities).where(signal_capabilities.c.signal_id == signal_id)
            )
            caps = sorted({capability_ids[c] for c in result.capabilities})
            if caps:
                s.execute(
                    insert(signal_capabilities).values(
                        [{"signal_id": signal_id, "capability_id": c} for c in caps]
                    )
                )
            info = {
                "prompt_version": prompt_id,
                "impact_rationale": result.impact_rationale,
                "new_entities": untracked(result.new_entities, tracked, sig.raw_text),
                "capabilities": list(result.capabilities),
                "quote_attempts": attempts,
            }
            if repair is not None:
                info["quote_repair"] = repair
            diff_meta: dict[str, Any] = {}
            if isinstance(result, EnrichedPageDiffBase):
                accepted, dropped = verify_price_changes(result.price_changes, sig.raw_text)
                diff_meta = {
                    "display_title": result.display_title,
                    "price_changes": accepted,
                    "price_changes_dropped": dropped,
                }
            s.execute(
                update(Signal)
                .where(Signal.id == signal_id)
                .values(
                    category=result.category,
                    summary=result.summary,
                    why_jfrog_cares=result.why_jfrog_cares,
                    takeaway=result.takeaway,
                    impact=result.impact,
                    evidence_quote=result.evidence_quote,
                    evidence_quote_verified=quote_ok,
                    is_claim=result.is_claim,
                    enriched_at=datetime.now(UTC),
                    enrich_prompt_version=prompt_id,
                    verification=Verification.PENDING,
                    verification_notes=None,
                    confidence=None,
                    meta=merge_meta(
                        {
                            "enrich": info,
                            "entity_review": review,
                            "quote_repaired": repair is not None,
                            **({"quote_side": side} if side else {}),
                            **({"quote_section": section} if section else {}),
                            **diff_meta,
                        }
                    )
                    .op("-")("enrich_error")
                    .op("-")("inherited_from"),
                )
            )
        return "enriched" if quote_ok else "enriched_quote_unverified"

    counts = run_parallel(
        "enrich", ids, worker, factory=factory, max_workers=ctx.config.llm.max_workers
    )
    counts["inherited"] = inherit_from_canonicals(session)
    logger.info("enrich: %s", counts)
    return counts


DIFF_PROMPT = "enrich_page_diff"
DISCOVERY_PROMPT = "enrich_discovery"


def is_discovery(meta: dict[str, Any] | None) -> bool:
    return bool(meta) and meta.get("kind") == "discovery"


def _page_diff_sql() -> Any:
    return Signal.meta["kind"].astext == "page_diff"


def tracked_terms(session: Session) -> set[str]:
    """Case-folded names and aliases of every entity we know (any review status)."""
    terms: set[str] = set()
    for name, aliases in session.execute(select(Entity.name, Entity.aliases)):
        terms.update(t.casefold() for t in (name, *(aliases or [])))
    return terms


def untracked(
    new_entities: list[Any], tracked: set[str], raw_text: str | None = None
) -> list[dict[str, Any]]:
    """Drop "new" entities we already track (the model sometimes lists them anyway). A url
    (discovery items) is kept only if it appears in the source text; otherwise it is set to None
    (meta url_dropped) – the radar must not follow a URL the model made up."""
    out = []
    folded = (raw_text or "").casefold()
    for e in new_entities:
        data = e if isinstance(e, dict) else e.model_dump()
        if data["name"].strip().casefold() in tracked:
            continue
        url = data.get("url")
        if url and raw_text is not None and url.strip().rstrip("/").casefold() not in folded:
            data = {**data, "url": None, "url_dropped": url}
        out.append(data)
    return out


def link_roles[K](refs: list[tuple[K, str]], source_entity: K | None) -> dict[K, bool]:
    """Final links {entity: is_primary}: the model's entities (primary wins over mention) plus the
    source's own entity, always primary. Used by apply_entity_links and the eval runner."""
    final: dict[K, bool] = {}
    for key, role in refs:
        final[key] = final.get(key, False) or role == "primary"
    if source_entity is not None:
        final[source_entity] = True
    return final


def apply_entity_links(
    s: Session,
    signal_id: int,
    result: EnrichedSignalBase,
    entity_ids: dict[str, int],
    source_entity_id: int | None,
    meta: dict[str, Any],
) -> dict[str, list[str]]:
    """Replace the signal's links with the model's, keeping the source entity as primary."""
    slug_by_id = {v: k for k, v in entity_ids.items()}
    final = link_roles(
        [(entity_ids[ref.slug], ref.role) for ref in result.entities], source_entity_id
    )

    s.execute(
        delete(signal_entities).where(
            signal_entities.c.signal_id == signal_id,
            signal_entities.c.entity_id.not_in(list(final) or [-1]),
        )
    )
    if final:
        stmt = insert(signal_entities).values(
            [{"signal_id": signal_id, "entity_id": e, "is_primary": p} for e, p in final.items()]
        )
        s.execute(
            stmt.on_conflict_do_update(
                index_elements=["signal_id", "entity_id"],
                set_={"is_primary": stmt.excluded.is_primary},
            )
        )

    rule = {link["entity_id"] for link in (meta.get("entity_links") or {}).get("links", [])}
    llm = {entity_ids[ref.slug] for ref in result.entities}
    return {
        "confirmed": sorted(slug_by_id[e] for e in rule & llm),
        "added": sorted(slug_by_id[e] for e in llm - rule),
        "dropped": sorted(slug_by_id[e] for e in rule - llm if e != source_entity_id),
    }


# ---------- duplicates ----------

_INHERITED = (
    "category",
    "summary",
    "why_jfrog_cares",
    "takeaway",
    "impact",
    "evidence_quote",
    "is_claim",
    "enriched_at",
    "enrich_prompt_version",
    "verification",
    "verification_notes",
)


def inherit_from_canonicals(session: Session) -> int:
    """Duplicates copy their canonical's enrichment, verification, entity links and capabilities.

    The quote check is redone against the duplicate's own text. Returns duplicates updated.
    """
    canonical = aliased(Signal)
    stale = or_(*(getattr(Signal, f).is_distinct_from(getattr(canonical, f)) for f in _INHERITED))
    rows = session.execute(
        select(Signal.id, Signal.raw_text, canonical)
        .join(canonical, canonical.id == Signal.dedup_cluster_id)
        .where(
            in_scope(),
            Signal.id != canonical.id,
            and_(canonical.enriched_at.is_not(None), Signal.relevant.is_(True)),
            stale,
        )
    ).all()
    for dup_id, raw_text, head in rows:
        values = {f: getattr(head, f) for f in _INHERITED}
        values["evidence_quote_verified"] = bool(
            head.evidence_quote and quote_in_text(head.evidence_quote, raw_text)
        )
        values["confidence"] = None
        values["meta"] = merge_meta(
            {
                "inherited_from": head.id,
                "enrich": head.meta.get("enrich"),
                "verify": head.meta.get("verify"),
            }
        )
        session.execute(update(Signal).where(Signal.id == dup_id).values(**values))
        for table in (signal_entities, signal_capabilities):
            src = session.execute(select(table).where(table.c.signal_id == head.id)).mappings()
            copies = [{**r, "signal_id": dup_id} for r in src]
            if copies:
                session.execute(insert(table).values(copies).on_conflict_do_nothing())
    session.flush()
    return len(rows)
