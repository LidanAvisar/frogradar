"""Emergence score of a radar candidate: transparent, computed in code, weights in config (D59).

    score = 100 x sum(weight_i x factor_i), every factor in [0, 1]

    mentions          ln(1 + m) / ln(1 + mentions_full_at)                    m = mention signals
    source_diversity  distinct sources / sources_full_at
    mention_velocity  share x min(1, recent / velocity_recent_full_at),
                      share = recent / (recent + prior x R / (W - R)); recent = mentions in the
                      last R days, prior = mentions in the W - R days before
    traction          max(stars: ln(1 + stars/day) / ln(1 + stars_per_day_full_at),
                          HN:    ln(1 + max points) / ln(1 + hn_points_full_at))
    segment_overlap   max segment weight over the candidate's segments (from triage)
    recency           0.5 ^ (days since last mention / recency_half_life_days)

Every factor is capped at 1. The breakdown (value, factor, weight, points) is stored with the
score and shown in the API, so an analyst can see why a candidate ranks where it does.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.catalog import RadarScoreSettings
from app.pipeline.radar.stars import Velocity

FORMULA = "radar_score.v1"


@dataclass(frozen=True)
class ScoreInput:
    mention_times: list[datetime]
    sources: int
    segments: list[str]
    last_seen: datetime
    stars: Velocity | None = None
    hn_points_max: int | None = None


def _log_ratio(value: float, full: float) -> float:
    return min(1.0, math.log1p(max(0.0, value)) / math.log1p(full))


def compute(
    inp: ScoreInput, cfg: RadarScoreSettings, now: datetime
) -> tuple[float, dict[str, Any]]:
    w = cfg.weights
    m = len(inp.mention_times)
    recent_from = now - timedelta(days=cfg.velocity_recent_days)
    window_from = now - timedelta(days=cfg.velocity_window_days)
    recent = sum(t >= recent_from for t in inp.mention_times)
    prior = sum(window_from <= t < recent_from for t in inp.mention_times)
    prior_scaled = (
        prior * cfg.velocity_recent_days / (cfg.velocity_window_days - cfg.velocity_recent_days)
    )
    share = recent / (recent + prior_scaled) if recent + prior_scaled > 0 else 0.0
    velocity = share * min(1.0, recent / cfg.velocity_recent_full_at)

    star_f = _log_ratio(inp.stars.per_day, cfg.stars_per_day_full_at) if inp.stars else 0.0
    hn_f = _log_ratio(inp.hn_points_max or 0, cfg.hn_points_full_at)
    weights = cfg.segment_weights
    seg = [(s, weights.get(s, 0.0)) for s in inp.segments]  # type: ignore[call-overload]
    seg_f = max((v for _, v in seg), default=0.0)
    days = max(0.0, (now - inp.last_seen).total_seconds() / 86400)
    recency = 0.5 ** (days / cfg.recency_half_life_days)

    parts: dict[str, tuple[Any, float, float]] = {
        "mentions": ({"mentions": m}, _log_ratio(m, cfg.mentions_full_at), w.mentions),
        "source_diversity": (
            {"sources": inp.sources},
            min(1.0, inp.sources / cfg.sources_full_at),
            w.source_diversity,
        ),
        "mention_velocity": (
            {"recent": recent, "prior": prior, "recent_days": cfg.velocity_recent_days},
            velocity,
            w.mention_velocity,
        ),
        "traction": (
            {
                "stars": inp.stars.stars if inp.stars else None,
                "stars_per_day": round(inp.stars.per_day, 2) if inp.stars else None,
                "stars_basis": inp.stars.basis if inp.stars else None,
                "hn_points_max": inp.hn_points_max,
                "from": "stars" if star_f >= hn_f and inp.stars else ("hn" if hn_f > 0 else None),
            },
            max(star_f, hn_f),
            w.traction,
        ),
        "segment_overlap": ({"segments": sorted(inp.segments)}, seg_f, w.segment_overlap),
        "recency": ({"days_since_last": round(days, 1)}, recency, w.recency),
    }
    breakdown: dict[str, Any] = {"formula": FORMULA, "components": {}}
    total = 0.0
    for name, (value, factor, weight) in parts.items():
        points = 100 * weight * factor
        total += points
        breakdown["components"][name] = {
            "value": value,
            "factor": round(factor, 4),
            "weight": weight,
            "points": round(points, 2),
        }
    score = round(total, 1)
    breakdown["score"] = score
    return score, breakdown
