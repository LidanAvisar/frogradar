"""Score: confidence per enriched, verified signal – computed in code, never by an LLM.

Confidence means: how faithfully the signal reflects what its source says. It is NOT a judgement
of whether the source's claim is true. Vendor marketing claims are flagged separately by is_claim
(shown as a "vendor claim" label) and do not change confidence.

    V = verification value (supported 1.0 / partial 0.5 / unsupported 0.0)
    T = source trust_score (no source → no_source_trust)
    C = corroboration: min(1, (sources in the dedup cluster − 1) / (corroboration_full_at − 1))
    E = 1 if the evidence quote is verbatim in the source, else 0
    confidence = w_V·V + w_T·T + w_C·C + w_E·E
    then capped: unsupported ≤ caps.unsupported, unverified quote ≤ caps.quote_unverified
    band: ≥ bands.high → high, ≥ bands.medium → medium, else low

Weights, caps and bands live in config/pipeline.yaml. The breakdown is stored in meta.score so
the UI can explain every number. Deterministic: recomputed on every run, counts what changed.
"""

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, aliased

from app.core.catalog import ScoreSettings
from app.db.enums import Verification
from app.db.models import Signal, Source
from app.pipeline.context import RunContext
from app.pipeline.llm_steps import merge_meta
from app.pipeline.scope import in_scope

logger = logging.getLogger(__name__)

FORMULA_VERSION = 1


@dataclass(frozen=True)
class Score:
    confidence: float
    band: str
    components: dict[str, float]
    caps_applied: list[str]

    def as_meta(self) -> dict[str, Any]:
        return {
            "confidence": self.confidence,
            "band": self.band,
            "components": self.components,
            "caps_applied": self.caps_applied,
            "formula_version": FORMULA_VERSION,
        }


def band(confidence: float, cfg: ScoreSettings) -> str:
    if confidence >= cfg.bands.high:
        return "high"
    if confidence >= cfg.bands.medium:
        return "medium"
    return "low"


def compute(
    cfg: ScoreSettings,
    *,
    verification: Verification,
    trust: float | None,
    sources_in_cluster: int,
    quote_verified: bool,
) -> Score:
    if verification is Verification.PENDING:
        raise ValueError("pending signals have no confidence")
    w = cfg.weights
    v = getattr(cfg.verification_values, verification.value)
    t = cfg.no_source_trust if trust is None else trust
    c = min(1.0, max(0, sources_in_cluster - 1) / (cfg.corroboration_full_at - 1))
    e = 1.0 if quote_verified else 0.0
    raw = w.verification * v + w.trust * t + w.corroboration * c + w.evidence * e
    caps = []
    if verification is Verification.UNSUPPORTED and raw > cfg.caps.unsupported:
        raw, caps = cfg.caps.unsupported, [*caps, "unsupported"]
    if not quote_verified and raw > cfg.caps.quote_unverified:
        raw, caps = cfg.caps.quote_unverified, [*caps, "quote_unverified"]
    confidence = round(raw, 3)
    return Score(
        confidence=confidence,
        band=band(confidence, cfg),
        components={"verification": v, "trust": t, "corroboration": c, "evidence": e},
        caps_applied=caps,
    )


def score_signals(session: Session, ctx: RunContext) -> dict[str, Any]:
    cfg = ctx.config.score
    head = aliased(Signal)
    sources_in_cluster = (
        select(func.count(func.distinct(head.source_id)))
        .where(head.dedup_cluster_id == Signal.dedup_cluster_id, head.source_id.is_not(None))
        .correlate(Signal)
        .scalar_subquery()
    )
    rows = session.execute(
        select(
            Signal.id,
            Signal.verification,
            Signal.evidence_quote_verified,
            Signal.confidence,
            Signal.meta["score"],
            Source.trust_score,
            sources_in_cluster,
            Signal.dedup_cluster_id,
        )
        .select_from(Signal)
        .outerjoin(Source, Source.id == Signal.source_id)
        .where(in_scope(), Signal.enriched_at.is_not(None))
    ).all()
    counts = {"scored": 0, "changed": 0, "pending": 0, "high": 0, "medium": 0, "low": 0}
    for sid, verification, quote_ok, old, old_meta, trust, n_sources, cluster in rows:
        if verification == Verification.PENDING:
            counts["pending"] += 1
            continue
        score = compute(
            cfg,
            verification=Verification(verification),
            trust=trust,
            sources_in_cluster=(n_sources or 1) if cluster is not None else 1,
            quote_verified=bool(quote_ok),
        )
        counts["scored"] += 1
        counts[score.band] += 1
        if old != score.confidence or old_meta != score.as_meta():
            counts["changed"] += 1
            session.execute(
                update(Signal)
                .where(Signal.id == sid)
                .values(confidence=score.confidence, meta=merge_meta({"score": score.as_meta()}))
            )
    session.flush()
    logger.info("score: %s", counts)
    return counts
