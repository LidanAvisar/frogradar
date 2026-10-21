"""Battlecards node: one battlecard report per tracked competitor (synthesis model), plus the
competitor's capability levels in capability_matrix.

- Competitors: tracked, approved companies with relationship competitor or coopetition that have
  at least `min_primary_signals` usable signals where the company or one of its products is
  primary (a mention alone doesn't make a battlecard).
- Input: the competitor's usable signals of the last `lookback_days` (company or product linked,
  primary or mention), highest impact first, at most `max_signals`.
- Due: no card yet, the last card is older than `refresh_days`, or an input signal with impact >=
  `trigger_impact` was not in the last card's input; --force battlecards regenerates all.
- Checks: citations (any id outside the competitor's input drops the item); capability levels need
  an evidence id from the input on which the competitor is PRIMARY; then one verify-model call
  checks every remaining item and level against its cited signals (supported → kept).
- Accepted levels are upserted into capability_matrix (evidence_signal_id, last_verified_at;
  previous_level / level_changed_at on a change). Cells not mentioned keep their old value.
- Each competitor runs in its own transaction: one failure is counted, the others go on.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.catalog import load_llm_routing, load_self_capabilities
from app.core.llm import BudgetExceeded, load_prompt
from app.db.enums import CapabilityLevel, EntityReviewStatus, EntityType, Relationship, ReportKind
from app.db.models import Capability, CapabilityMatrix, Entity, Report, Signal, signal_entities
from app.pipeline.context import RunContext
from app.pipeline.prompting import Catalog
from app.pipeline.scope import event_time, usable
from app.pipeline.synthesis.check import verify_claims
from app.pipeline.synthesis.citations import Checked, Claim, check_citations, classify_ids
from app.pipeline.synthesis.inputs import facts, signals_block
from app.pipeline.synthesis.schemas import SECTIONS, BattlecardBase, battlecard_schema

logger = logging.getLogger(__name__)

PROMPT = "battlecard"
RIVALS = (Relationship.COMPETITOR, Relationship.COOPETITION)


@dataclass(frozen=True)
class Competitor:
    id: int
    slug: str
    name: str
    relationship: str
    entity_ids: tuple[int, ...]  # company + products
    products: tuple[str, ...]


def competitors(session: Session) -> list[Competitor]:
    rows = session.scalars(
        select(Entity)
        .where(
            Entity.type == EntityType.COMPANY,
            Entity.is_tracked,
            Entity.review_status == EntityReviewStatus.APPROVED,
            Entity.relationship.in_(RIVALS),
            ~Entity.is_self,
        )
        .order_by(Entity.slug)
    ).all()
    out = []
    for e in rows:
        products = session.scalars(
            select(Entity).where(Entity.parent_id == e.id).order_by(Entity.name)
        ).all()
        out.append(
            Competitor(
                e.id,
                e.slug,
                e.name,
                str(e.relationship),
                (e.id, *(p.id for p in products)),
                tuple(p.name for p in products),
            )
        )
    return out


def input_signals(
    session: Session, comp: Competitor, since: datetime, limit: int
) -> tuple[list[Signal], set[int], set[int]]:
    """(input signals, ids where the competitor is primary, ALL usable ids linked to it)."""
    linked = (
        select(signal_entities.c.signal_id)
        .where(signal_entities.c.entity_id.in_(comp.entity_ids))
        .scalar_subquery()
    )
    signals = session.scalars(
        select(Signal)
        .where(usable(), Signal.id.in_(linked), event_time() >= since)
        .order_by(Signal.impact.desc().nulls_last(), event_time().desc(), Signal.id.desc())
        .limit(limit)
    ).all()
    ids = [s.id for s in signals]
    primary = set(
        session.scalars(
            select(signal_entities.c.signal_id).where(
                signal_entities.c.signal_id.in_(ids),
                signal_entities.c.entity_id.in_(comp.entity_ids),
                signal_entities.c.is_primary,
            )
        )
    )
    all_linked = set(session.scalars(select(Signal.id).where(usable(), Signal.id.in_(linked))))
    return list(signals), primary, all_linked


def last_card(session: Session, entity_id: int) -> Report | None:
    return session.scalar(
        select(Report)
        .where(Report.kind == ReportKind.BATTLECARD, Report.entity_id == entity_id)
        .order_by(Report.created_at.desc(), Report.id.desc())
        .limit(1)
    )


def due_reason(card: Report | None, signals: Sequence[Signal], ctx: RunContext) -> str | None:
    cfg = ctx.config.synthesis.battlecards
    if "battlecards" in ctx.force:
        return "forced"
    if card is None:
        return "no card yet"
    if ctx.now - card.created_at >= timedelta(days=cfg.refresh_days):
        return "weekly refresh"
    seen = set((card.content or {}).get("input_ids") or [])
    new = [s.id for s in signals if (s.impact or 0) >= cfg.trigger_impact and s.id not in seen]
    if new:
        return f"new impact >= {cfg.trigger_impact}: {new}"
    return None


def battlecards_step(session: Session, ctx: RunContext) -> dict[str, Any]:
    complete, factory = ctx.require_llm()
    cfg = ctx.config.synthesis.battlecards
    since = ctx.now - timedelta(days=cfg.lookback_days)
    counts: dict[str, Any] = {"stored": 0, "not_due": 0, "not_eligible": 0, "failed": 0}
    errors: dict[str, str] = {}
    for comp in competitors(session):
        signals, primary, _ = input_signals(session, comp, since, cfg.max_signals)
        if len(primary) < cfg.min_primary_signals:
            counts["not_eligible"] += 1
            continue
        reason = due_reason(last_card(session, comp.id), signals, ctx)
        if reason is None:
            counts["not_due"] += 1
            continue
        try:
            with factory.begin() as s:
                result = build_card(s, comp, ctx, since)
            counts["stored"] += 1
            counts[comp.slug] = {"due": reason, **result}
        except BudgetExceeded:
            raise
        except Exception as e:  # noqa: BLE001 - one competitor must not stop the others
            logger.exception("battlecard %s failed", comp.slug)
            counts["failed"] += 1
            errors[comp.slug] = f"{type(e).__name__}: {e}"[:300]
    if errors:
        counts["errors"] = errors
    logger.info("battlecards: %s", counts)
    return counts


def build_card(s: Session, comp: Competitor, ctx: RunContext, since: datetime) -> dict[str, Any]:
    complete, _ = ctx.require_llm()
    cfg = ctx.config.synthesis.battlecards
    signals, primary, linked = input_signals(s, comp, since, cfg.max_signals)
    items = facts(s, signals)
    by_id = {f.id: f for f in items}
    caps = s.scalars(select(Capability).order_by(Capability.segment, Capability.slug)).all()
    cap_name = {c.slug: c.name for c in caps}
    schema = battlecard_schema(list(cap_name))

    prompt = load_prompt(PROMPT)
    messages = prompt.messages(
        competitor=comp.name,
        relationship=comp.relationship,
        products=", ".join(comp.products) or "-",
        jfrog_portfolio=Catalog.load(s).jfrog_portfolio,
        jfrog_levels=_jfrog_levels(cap_name),
        capabilities="\n".join(
            f"{c.slug} | {c.name} | {c.segment} | {c.description}" for c in caps
        ),
        count=len(items),
        **signals_block(items, with_capabilities=True),
    )
    card: BattlecardBase = complete("synthesis", PROMPT, messages, schema, prompt_version=prompt.id)

    # 1. Citations: every id must be one of this competitor's input signals.
    claims = [Claim("positioning", card.positioning.text, tuple(card.positioning.signal_ids))]
    for section in SECTIONS:
        claims += [
            Claim(f"{section}.{i}", item.text, tuple(item.signal_ids))
            for i, item in enumerate(getattr(card, section), 1)
        ]
    cited = check_citations(s, claims, by_id, entity_signal_ids=linked)

    # 2. Capability levels: evidence from the input, competitor primary on it, one per slug.
    cap_checked = _check_capabilities(s, card, comp, by_id, primary, linked, cap_name)

    # 3. One verify-model call for everything still standing.
    checked, check_version = verify_claims(
        [*cited.kept, *cap_checked.kept],
        by_id,
        complete,
        kind="battlecard",
        subject=comp.name,
    )
    kept = {c.key: c for c in checked.kept}
    dropped = [*cited.dropped, *cap_checked.dropped, *checked.dropped]

    def section(name: str) -> list[dict[str, Any]]:
        return [
            {"text": c.text, "signal_ids": list(c.signal_ids)}
            for k, c in kept.items()
            if k.startswith(f"{name}.")
        ]

    accepted = [c.extra for k, c in kept.items() if k.startswith("capability.")]
    positioning = kept.get("positioning")
    content = {
        "positioning": (
            {"text": positioning.text, "signal_ids": list(positioning.signal_ids)}
            if positioning
            else None
        ),
        **{name: section(name) for name in SECTIONS},
        "capabilities_accepted": accepted,
        "capabilities_dropped": [d for d in dropped if d["key"].startswith("capability.")],
        "dropped": [d for d in dropped if not d["key"].startswith("capability.")],
        "input_ids": [f.id for f in items],
        "prompt_version": prompt.id,
        "check_prompt_version": check_version,
    }
    cited_ids = sorted({i for c in kept.values() for i in c.signal_ids})
    s.add(
        Report(
            kind=ReportKind.BATTLECARD,
            entity_id=comp.id,
            period_start=since,
            period_end=ctx.now,
            signal_ids=cited_ids,
            run_id=ctx.run_id,
            model=load_llm_routing().tasks.synthesis.model,
            content=content,
        )
    )
    matrix = upsert_levels(s, comp.id, accepted, ctx.now)
    s.flush()
    proposed_caps = len(card.capabilities)  # type: ignore[attr-defined]
    return {
        "input_signals": len(items),
        "items_kept": len(kept) - len(accepted),
        "items_dropped": len(content["dropped"]),
        "levels_accepted": len(accepted),
        "levels_dropped": proposed_caps - len(accepted),
        "matrix": matrix,
    }


def _jfrog_levels(cap_name: dict[str, str]) -> str:
    levels = load_self_capabilities().capabilities
    return "\n".join(
        f"{slug} | {cap_name.get(slug, slug)} | {cfg.level}" for slug, cfg in levels.items()
    )


def _check_capabilities(
    s: Session,
    card: BattlecardBase,
    comp: Competitor,
    by_id: dict[int, Any],
    primary: set[int],
    linked: set[int],
    cap_name: dict[str, str],
) -> Checked:
    out = Checked()
    seen: set[str] = set()
    claims = card.capabilities  # type: ignore[attr-defined]
    bad = {c.evidence_signal_id for c in claims if c.evidence_signal_id not in by_id}
    why = classify_ids(s, bad, entity_signal_ids=linked)
    for i, c in enumerate(claims, 1):
        extra = {
            "capability": c.capability,
            "level": c.level,
            "evidence_signal_id": c.evidence_signal_id,
            "note": c.note,
        }
        claim = Claim(
            f"capability.{i}",
            f"{comp.name} – {cap_name.get(c.capability, c.capability)}: {c.level}. {c.note}",
            (c.evidence_signal_id,),
            extra,
        )
        if c.capability not in cap_name:
            out.drop(claim, "unknown_capability")
        elif c.evidence_signal_id not in by_id:
            out.drop(
                claim,
                "invalid_citation",
                invalid_ids={str(c.evidence_signal_id): why[c.evidence_signal_id]},
            )
        elif c.evidence_signal_id not in primary:
            out.drop(claim, "evidence_not_about_competitor")
        elif c.capability in seen:
            out.drop(claim, "duplicate_capability")
        else:
            seen.add(c.capability)
            out.kept.append(claim)
    return out


def upsert_levels(
    s: Session, entity_id: int, accepted: list[dict[str, Any]], now: datetime
) -> dict[str, int]:
    counts = {"inserted": 0, "changed": 0, "confirmed": 0}
    cap_ids = dict(s.execute(select(Capability.slug, Capability.id)).all())
    existing = {
        r.capability_id: r
        for r in s.scalars(select(CapabilityMatrix).where(CapabilityMatrix.entity_id == entity_id))
    }
    for a in accepted:
        cap_id = cap_ids[a["capability"]]
        level = CapabilityLevel(a["level"])
        row = existing.get(cap_id)
        if row is None:
            row = CapabilityMatrix(entity_id=entity_id, capability_id=cap_id, level=level)
            s.add(row)
            existing[cap_id] = row
            counts["inserted"] += 1
        elif row.level != level:
            row.previous_level, row.level_changed_at = row.level, now
            row.level = level
            counts["changed"] += 1
        else:
            counts["confirmed"] += 1
        row.notes = a["note"]
        row.evidence_signal_id = a["evidence_signal_id"]
        row.last_verified_at = now
    return counts
