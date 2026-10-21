"""Radar node (after battlecards): observations → resolve / merge → metrics + score → profiles.

    python -m app.pipeline.run radar                 # make radar
    python -m app.pipeline.run radar --force radar   # regenerate profiles of the top candidates

Counts: observations, skipped_<reason>, created, matched_<rule>, merged, scored, profiles
(ok / rejected / unchanged / failed / below_threshold).
"""

import logging
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.catalog import RadarSettings, load_discovery, load_llm_routing
from app.core.llm import BudgetExceeded, load_prompt
from app.db.enums import ProfileStatus, RadarStatus
from app.db.models import RadarCandidate, Signal
from app.pipeline.context import RunContext
from app.pipeline.radar import stars
from app.pipeline.radar.extract import observations
from app.pipeline.radar.normalize import repo_slug
from app.pipeline.radar.profile import PROMPT, build_profile, signal_hash
from app.pipeline.radar.resolve import Resolver
from app.pipeline.radar.score import ScoreInput, compute
from app.pipeline.scope import event_time

logger = logging.getLogger(__name__)

PROFILE_STATUSES = (RadarStatus.NEW, RadarStatus.WATCHING)


def radar_step(
    session: Session, ctx: RunContext, cfg: RadarSettings | None = None
) -> dict[str, Any]:
    cfg = cfg or load_discovery().radar
    embed = (lambda texts: ctx.embed(texts).vectors) if ctx.embed else None
    resolver = Resolver(session, cfg, embed, ctx.now)
    obs = observations(session)
    for o in obs:
        resolver.add(o)
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


def refresh_score(
    session: Session, cand: RadarCandidate, cfg: RadarSettings, ctx: RunContext
) -> None:
    times = list(
        session.scalars(select(event_time()).where(Signal.id.in_(cand.mention_signal_ids or [-1])))
    )
    velocity = None
    if (slug := repo_slug(cand.repo_url)) is not None:
        velocity = stars.velocity(session, slug, ctx.now)
    points = [int(p) for p in ((cand.meta or {}).get("hn_points") or {}).values()]
    inp = ScoreInput(
        mention_times=times,
        sources=len(cand.source_ids or []),
        segments=list(cand.segments or []),
        last_seen=cand.last_seen,
        stars=velocity,
        hn_points_max=max(points) if points else None,
    )
    score, breakdown = compute(inp, cfg.score, ctx.now)
    recent = breakdown["components"]["mention_velocity"]["value"]["recent"]
    cand.score, cand.score_breakdown = score, breakdown
    cand.metrics = {
        "mentions": len(times),
        "sources": len(cand.source_ids or []),
        "mentions_recent": recent,
        "stars": velocity.stars if velocity else None,
        "star_velocity": round(velocity.per_day, 2) if velocity else None,
        "star_velocity_basis": velocity.basis if velocity else None,
        "hn_points_max": inp.hn_points_max,
    }


def profiles(
    session: Session, ctx: RunContext, cfg: RadarSettings, live: list[RadarCandidate]
) -> dict[str, Any]:
    p = cfg.profile
    pool = sorted(
        (c for c in live if c.status in PROFILE_STATUSES and c.score is not None),
        key=lambda c: (-c.score, c.id),
    )
    eligible = [c for c in pool if c.score >= p.min_score][: p.top_n]
    counts: Counter[str] = Counter(below_threshold=len(pool) - len(eligible))
    if not eligible:
        return dict(counts)
    if ctx.complete is None:
        counts["skipped_no_llm"] = len(eligible)
        return dict(counts)
    model = load_llm_routing().tasks.bulk.model
    force = "radar" in ctx.force
    current = load_prompt(PROMPT).id
    for cand in eligible:
        # Same mentions and the same prompt version → keep the profile.
        unchanged = cand.profile_signal_hash == signal_hash(cand.mention_signal_ids) and (
            (cand.profile or {}).get("prompt_version") == current
        )
        if unchanged and cand.profile_status != ProfileStatus.NONE and not force:
            counts["unchanged"] += 1
            continue
        try:
            with session.begin_nested():
                status = build_profile(session, cand, p, ctx.complete, ctx.now, model=model)
            counts[status] += 1
        except BudgetExceeded as e:
            counts["stopped"] = f"budget: {e}"[:300]  # type: ignore[assignment]
            break
        except Exception as e:  # noqa: BLE001 - one candidate never stops the step
            logger.warning("profile failed for candidate %s: %s", cand.id, e)
            cand.meta = {**(cand.meta or {}), "profile_error": f"{type(e).__name__}: {e}"[:300]}
            counts["failed"] += 1
    return dict(counts)
