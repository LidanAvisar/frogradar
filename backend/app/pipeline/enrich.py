"""Enrich: relevant canonical signals get category, summary, why JFrog cares, impact, evidence
quote, is_claim, entity links, new-entity candidates and capabilities (bulk model).

- Selection: in scope, canonical (or not clustered), relevant, and not yet enriched with the
  current prompt version (or everything with --force enrich).
- Evidence quote: checked in code against raw_text (app.pipeline.quotes). On failure the model gets
  the error and tries again (quote_retries); still failing → stored with
  evidence_quote_verified=false (confidence is capped; the UI must not show it as a quote).
- Entities: the model confirms/corrects the rule-based links (slugs validated by the schema).
  Final links = the model's list + the source's own entity (always primary). Rule links the model
  didn't confirm are removed; meta.entity_review records confirmed / added / dropped.
- Re-enrichment resets verification to pending, so the new text is verified again.
- Duplicates inherit everything from their canonical (inherit_from_canonicals).
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from app.core.llm import load_prompt
from app.core.llm.structured import retry_messages
from app.db.enums import Verification
from app.db.models import Capability, Entity, Signal, Source, signal_capabilities, signal_entities
from app.pipeline.context import RunContext
from app.pipeline.llm_steps import canonical_or_unclustered, load_input, merge_meta, run_parallel
from app.pipeline.prompting import Catalog, enrich_messages
from app.pipeline.quotes import quote_error, quote_in_text
from app.pipeline.schemas import EnrichedSignalBase, enrich_schema
from app.pipeline.scope import in_scope

logger = logging.getLogger(__name__)


def enrich_signals(session: Session, ctx: RunContext) -> dict[str, Any]:
    complete, factory = ctx.require_llm()
    version = load_prompt("enrich_signal").id
    cfg = ctx.config.enrich
    todo = [in_scope(), canonical_or_unclustered(), Signal.relevant.is_(True)]
    if "enrich" not in ctx.force:
        todo.append(
            or_(
                Signal.enriched_at.is_(None), Signal.enrich_prompt_version.is_distinct_from(version)
            )
        )
    ids = session.scalars(select(Signal.id).where(*todo).order_by(Signal.id)).all()
    catalog = Catalog.load(session)
    schema = enrich_schema(catalog.entity_slugs, catalog.capability_slugs)
    entity_ids = dict(session.execute(select(Entity.slug, Entity.id)).all())
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
        prompt, messages = enrich_messages(sig, catalog, cfg.max_chars)
        result = complete("bulk", "enrich_signal", messages, schema, prompt_version=prompt.id)
        attempts = 1
        quote_ok = quote_in_text(result.evidence_quote, sig.raw_text)
        while not quote_ok and attempts <= cfg.quote_retries:
            retry = [
                *messages,
                *retry_messages(result.model_dump_json(), quote_error(result.evidence_quote)),
            ]
            result = complete("bulk", "enrich_signal", retry, schema, prompt_version=prompt.id)
            attempts += 1
            quote_ok = quote_in_text(result.evidence_quote, sig.raw_text)

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
                "prompt_version": prompt.id,
                "impact_rationale": result.impact_rationale,
                "new_entities": [e.model_dump() for e in result.new_entities],
                "capabilities": list(result.capabilities),
                "quote_attempts": attempts,
            }
            s.execute(
                update(Signal)
                .where(Signal.id == signal_id)
                .values(
                    category=result.category,
                    summary=result.summary,
                    why_jfrog_cares=result.why_jfrog_cares,
                    impact=result.impact,
                    evidence_quote=result.evidence_quote,
                    evidence_quote_verified=quote_ok,
                    is_claim=result.is_claim,
                    enriched_at=datetime.now(UTC),
                    enrich_prompt_version=prompt.id,
                    verification=Verification.PENDING,
                    verification_notes=None,
                    confidence=None,
                    meta=merge_meta({"enrich": info, "entity_review": review})
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
    final: dict[int, bool] = {}
    for ref in result.entities:
        eid = entity_ids[ref.slug]
        final[eid] = final.get(eid, False) or ref.role == "primary"
    if source_entity_id is not None:
        final[source_entity_id] = True

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
