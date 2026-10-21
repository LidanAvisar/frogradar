"""Radar node (after battlecards): observations → resolve / prune / merge → metrics + score →
profiles → rescore.

    python -m app.pipeline.run radar                 # make radar
    python -m app.pipeline.run radar --force radar   # regenerate profiles of the top candidates

Profiles change the score (the JFrog relation drives the relevance factor, radar_score.v2), so
the step profiles the top candidates, rescores them, and profiles once more if new candidates
moved into the top N.

Counts: observations, skipped_<reason>, created, matched_<rule>, merged, mentions_pruned,
candidates_removed, profiles (ok / rejected / unchanged / failed / below_threshold).
"""

import logging
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.catalog import RadarSettings, SourceCredibility, load_discovery, load_llm_routing
from app.core.llm import BudgetExceeded, load_prompt
from app.db.enums import DISCOVERY_METHODS, ProfileStatus, RadarStatus
from app.db.models import RadarCandidate, Signal, Source
from app.pipeline.context import RunContext
from app.pipeline.radar import stars
from app.pipeline.radar.extract import observations
from app.pipeline.radar.normalize import repo_slug
from app.pipeline.radar.profile import PROMPT, build_profile, profile_relation, signal_hash
from app.pipeline.radar.resolve import Resolver
from app.pipeline.radar.score import Mention, ScoreInput, compute
from app.pipeline.scope import event_time

logger = logging.getLogger(__name__)

PROFILE_STATUSES = (RadarStatus.NEW, RadarStatus.WATCHING)
PROFILE_ROUNDS = 2


def radar_step(
    session: Session, ctx: RunContext, cfg: RadarSettings | None = None
) -> dict[str, Any]:
    cfg = cfg or load_discovery().radar
    embed = (lambda texts: ctx.embed(texts).vectors) if ctx.embed else None
    resolver = Resolver(session, cfg, embed, ctx.now)
    obs = observations(session)
    for o in obs:
        resolver.add(o)
    resolver.prune()
    resolver.merge_pass()
    session.flush()

    for cand in resolver.live:
        refresh_score(session, cand, cfg, ctx)
    session.flush()

    counts: dict[str, Any] = {"observations": len(obs), **dict(sorted(resolver.counts.items()))}
    counts["live_candidates"] = len(resolver.live)
    counts["profiles"] = profiles(session, ctx, cfg, resolver.live)
    logger.info("radar: %s", counts)
    return counts


def credibility(cfg: SourceCredibility, kind: str | None, method: str | None) -> float:
    if method in DISCOVERY_METHODS:
        return cfg.discovery
    if kind is None:
        return cfg.no_source
    return float(getattr(cfg, str(kind), cfg.community))


def refresh_score(
    session: Session, cand: RadarCandidate, cfg: RadarSettings, ctx: RunContext
) -> None:
    sc = cfg.score
    rows = session.execute(
        select(
            Signal.id,
            event_time().label("at"),
            Signal.impact,
            Signal.enriched_at,
            Signal.source_id,
            Source.kind,
            Source.fetch_method,
        )
        .outerjoin(Source, Source.id == Signal.source_id)
        .where(Signal.id.in_(cand.mention_signal_ids or [-1]))
    ).all()
    mentions = [
        Mention(
            at=r.at,
            credibility=credibility(sc.credibility, r.kind, r.fetch_method),
            impact=r.impact if r.enriched_at is not None else None,
        )
        for r in rows
    ]
    by_source = {r.source_id: credibility(sc.credibility, r.kind, r.fetch_method) for r in rows}
    if rows:  # mentions may have been pruned: dates and sources follow the remaining ones
        cand.source_ids = sorted(s for s in by_source if s is not None)
        cand.first_seen = min(r.at for r in rows)
        cand.last_seen = max(r.at for r in rows)

    velocity = None
    if (slug := repo_slug(cand.repo_url)) is not None:
        velocity = stars.velocity(session, slug, ctx.now)
    points = [int(p) for p in ((cand.meta or {}).get("hn_points") or {}).values()]
    relation = profile_relation(cand.profile) if cand.profile_status != ProfileStatus.NONE else None
    inp = ScoreInput(
        mentions=mentions,
        source_credibility=list(by_source.values()),
        last_seen=cand.last_seen,
        stars=velocity,
        hn_points_max=max(points) if points else None,
        relation=relation,
    )
    score, breakdown = compute(inp, sc, ctx.now)
    comps = breakdown["components"]
    cand.score, cand.score_breakdown = score, breakdown
    cand.metrics = {
        "mentions": len(mentions),
        "sources": len(by_source),
        "mentions_recent": comps["mention_velocity"]["value"]["recent"],
        "max_impact": comps["relevance"]["value"]["max_impact"],
        "relation": relation,
        "stars": velocity.stars if velocity else None,
        "star_velocity": round(velocity.per_day, 2) if velocity else None,
        "star_velocity_basis": velocity.basis if velocity else None,
        "hn_points_max": inp.hn_points_max,
    }


def _needs_profile(cand: RadarCandidate, current: str, force: bool) -> bool:
    unchanged = cand.profile_signal_hash == signal_hash(cand.mention_signal_ids) and (
        (cand.profile or {}).get("prompt_version") == current
    )
    return force or not unchanged or cand.profile_status == ProfileStatus.NONE


def profiles(
    session: Session, ctx: RunContext, cfg: RadarSettings, live: list[RadarCandidate]
) -> dict[str, Any]:
    p = cfg.profile
    counts: Counter[str] = Counter()
    if ctx.complete is None:
        counts["skipped_no_llm"] = 1
        return dict(counts)
    model = load_llm_routing().tasks.bulk.model
    force = "radar" in ctx.force
    current = load_prompt(PROMPT).id
    done: set[int] = set()
    for _ in range(PROFILE_ROUNDS):
        pool = sorted(
            (c for c in live if c.status in PROFILE_STATUSES and c.score is not None),
            key=lambda c: (-c.score, c.id),
        )
        eligible = [c for c in pool if c.score >= p.min_score][: p.top_n]
        counts["below_threshold"] = len(pool) - len(eligible)
        todo = [c for c in eligible if c.id not in done and _needs_profile(c, current, force)]
        counts["unchanged"] += sum(1 for c in eligible if c.id not in done and c not in todo)
        done.update(c.id for c in eligible)
        if not todo:
            break
        for cand in todo:
            try:
                with session.begin_nested():
                    status = build_profile(session, cand, p, ctx.complete, ctx.now, model=model)
                counts[status] += 1
            except BudgetExceeded as e:
                counts["stopped"] = f"budget: {e}"[:300]  # type: ignore[assignment]
                return dict(counts)
            except Exception as e:  # noqa: BLE001 - one candidate never stops the step
                logger.warning("profile failed for candidate %s: %s", cand.id, e)
                cand.meta = {**(cand.meta or {}), "profile_error": f"{type(e).__name__}: {e}"[:300]}
                counts["failed"] += 1
            refresh_score(session, cand, cfg, ctx)  # the relation now drives relevance
        session.flush()
    return dict(counts)
