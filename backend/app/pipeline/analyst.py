"""Analyst corrections on signals (D68): what the model said is kept, what the analyst said wins.

meta.analyst = {by, at, action, reason_code, comment,
                fields: {field: {"from": model value, "to": analyst value}}}
meta.model_output = the model's own values of the correctable fields, saved before the first
correction and refreshed whenever triage / enrich run again (so nothing the model said is lost).

The pipeline calls apply_overrides() after it writes triage / enrich results, so a correction
survives re-enrichment (e.g. a prompt version bump).
"""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import Entity, Signal, signal_entities
from app.pipeline.llm_steps import merge_meta

FIELDS = ("category", "impact", "relevant", "primary_entity")
NO_ENTITY = "none"  # primary_entity value: the signal is about no tracked company


def primary_slug(session: Session, signal_id: int) -> str:
    slug = session.scalar(
        select(Entity.slug)
        .join(signal_entities, signal_entities.c.entity_id == Entity.id)
        .where(signal_entities.c.signal_id == signal_id, signal_entities.c.is_primary)
        .order_by(Entity.type, Entity.id)  # a company before its product
        .limit(1)
    )
    return slug or NO_ENTITY


def current_values(session: Session, sig: Signal) -> dict[str, Any]:
    category = sig.category.value if sig.category is not None else None
    return {
        "category": category,
        "impact": sig.impact,
        "relevant": sig.relevant,
        "primary_entity": primary_slug(session, sig.id),
    }


def set_primary(session: Session, signal_id: int, slug: str) -> None:
    """Make `slug` the only primary entity of the signal (linking it if needed); 'none' = no
    primary. Other links stay as mentions."""
    session.execute(
        update(signal_entities)
        .where(signal_entities.c.signal_id == signal_id)
        .values(is_primary=False)
    )
    if slug == NO_ENTITY:
        return
    entity_id = session.scalar(select(Entity.id).where(Entity.slug == slug))
    if entity_id is None:
        raise ValueError(f"unknown entity {slug!r}")
    linked = session.execute(
        update(signal_entities)
        .where(signal_entities.c.signal_id == signal_id, signal_entities.c.entity_id == entity_id)
        .values(is_primary=True)
    ).rowcount
    if not linked:
        session.execute(
            signal_entities.insert().values(
                signal_id=signal_id, entity_id=entity_id, is_primary=True
            )
        )


def write_values(session: Session, signal_id: int, values: dict[str, Any]) -> None:
    cols = {k: v for k, v in values.items() if k in ("category", "impact", "relevant")}
    if cols:
        session.execute(update(Signal).where(Signal.id == signal_id).values(**cols))
    if "primary_entity" in values:
        set_primary(session, signal_id, values["primary_entity"])


def corrected(meta: dict[str, Any] | None) -> dict[str, Any]:
    """{field: analyst value} of the signal's correction ({} if none)."""
    fields = ((meta or {}).get("analyst") or {}).get("fields") or {}
    return {f: v["to"] for f, v in fields.items() if f in FIELDS and isinstance(v, dict)}


STAGE_FIELDS = {"triage": ("relevant",), "enrich": ("category", "impact", "primary_entity")}


def apply_overrides(
    session: Session, signal_id: int, stage: str, prompt_version: str | None = None
) -> bool:
    """Pipeline hook: `stage` (triage / enrich) just wrote fresh model values. Save those as
    meta.model_output and put the analyst's corrected values back. Fields the stage does not
    write keep their earlier model_output. Returns True if the signal had a correction."""
    sig = session.get(Signal, signal_id, populate_existing=True)
    if sig is None:
        return False
    wanted = corrected(sig.meta)
    if not wanted:
        return False
    fresh = current_values(session, sig)
    model = dict((sig.meta or {}).get("model_output") or {})
    model.update({f: fresh[f] for f in STAGE_FIELDS[stage]})
    if prompt_version:
        model[f"{stage}_prompt_version"] = prompt_version
    write_values(session, signal_id, wanted)
    meta = merge_meta({"model_output": model})
    session.execute(update(Signal).where(Signal.id == signal_id).values(meta=meta))
    return True
