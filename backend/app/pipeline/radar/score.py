"""Emergence score of a radar candidate: transparent, computed in code, weights in config (D59).

radar_score.v2 – relevance to JFrog first, popularity second:

    score = 100 x sum(weight_i x factor_i), every factor in [0, 1]

    relevance         0.35  before a profile: (max enrich impact of the relevant mentions - 1) / 4
                            with a profile relation: relation_share x relation weight
                            + (1 - relation_share) x impact factor
                            (threatens 1.0, both 1.0, complements 0.6, none 0.05)
    mentions          0.10  ln(1 + weighted mentions) / ln(1 + mentions_full_at)
    source_diversity  0.20  weighted distinct sources / sources_full_at
    mention_velocity  0.10  share x min(1, recent / velocity_recent_full_at),
                            share = recent / (recent + prior x R / (W - R)); recent = mentions
                            in the last R days, prior = mentions in the W - R days before
    traction          0.10  max(stars: ln(1 + stars/day) / ln(1 + stars_per_day_full_at),
                                HN: min(hn_max_factor,
                                        ln(1 + max points) / ln(1 + hn_points_full_at)))
    recency           0.15  0.5 ^ (days since last mention / recency_half_life_days)

Weighted = each mention / source counts its credibility (press, official 1.0; community, GitHub
0.5; discovery sources 0.35). Every factor is capped at 1. The breakdown (value, factor, weight,
points) is stored with the score and shown in the API.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.catalog import RadarScoreSettings
from app.pipeline.radar.stars import Velocity

FORMULA = "radar_score.v2"


@dataclass(frozen=True)
class Mention:
    at: datetime
    credibility: float
    impact: int | None = None  # enrich impact of the signal, if enriched


@dataclass(frozen=True)
class ScoreInput:
    mentions: list[Mention]
    source_credibility: list[float]  # one per distinct source
    last_seen: datetime
    stars: Velocity | None = None
    hn_points_max: int | None = None
    relation: str | None = None  # from the profile (kept or only partial), else None


def _log_ratio(value: float, full: float) -> float:
    return min(1.0, math.log1p(max(0.0, value)) / math.log1p(full))


def compute(
    inp: ScoreInput, cfg: RadarScoreSettings, now: datetime
) -> tuple[float, dict[str, Any]]:
    w = cfg.weights
    times = [m.at for m in inp.mentions]
    recent_from = now - timedelta(days=cfg.velocity_recent_days)
    window_from = now - timedelta(days=cfg.velocity_window_days)
    recent = sum(t >= recent_from for t in times)
    prior = sum(window_from <= t < recent_from for t in times)
    prior_scaled = (
        prior * cfg.velocity_recent_days / (cfg.velocity_window_days - cfg.velocity_recent_days)
    )
    share = recent / (recent + prior_scaled) if recent + prior_scaled > 0 else 0.0
    velocity = share * min(1.0, recent / cfg.velocity_recent_full_at)

    impacts = [m.impact for m in inp.mentions if m.impact is not None]
    max_impact = max(impacts) if impacts else None
    impact_f = (max_impact - 1) / 4 if max_impact is not None else 0.0
    relation_w = getattr(cfg.relation_weights, inp.relation) if inp.relation else None
    if relation_w is None:
        relevance = impact_f
    else:
        relevance = cfg.relation_share * relation_w + (1 - cfg.relation_share) * impact_f

    weighted_mentions = sum(m.credibility for m in inp.mentions)
    weighted_sources = sum(inp.source_credibility)
    star_f = _log_ratio(inp.stars.per_day, cfg.stars_per_day_full_at) if inp.stars else 0.0
    hn_f = min(cfg.hn_max_factor, _log_ratio(inp.hn_points_max or 0, cfg.hn_points_full_at))
    days = max(0.0, (now - inp.last_seen).total_seconds() / 86400)
    recency = 0.5 ** (days / cfg.recency_half_life_days)

    parts: dict[str, tuple[Any, float, float]] = {
        "relevance": (
            {
                "max_impact": max_impact,
                "relation": inp.relation,
                "basis": "profile_relation" if relation_w is not None else "impact",
            },
            relevance,
            w.relevance,
        ),
        "mentions": (
            {"mentions": len(inp.mentions), "weighted": round(weighted_mentions, 2)},
            _log_ratio(weighted_mentions, cfg.mentions_full_at),
            w.mentions,
        ),
        "source_diversity": (
            {"sources": len(inp.source_credibility), "weighted": round(weighted_sources, 2)},
            min(1.0, weighted_sources / cfg.sources_full_at),
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
