"""Which API keys are present and which features they enable (startup log, /api/status, CLI).

Only booleans and env var names leave this module, never key values. The required keys (the
providers of the four model tiers) are checked before the app starts (app.core.preflight); an
optional key that is missing turns only its feature off or degraded, with a reason, and its
endpoints answer with that reason instead of failing inside a provider call.

    python -m app.core.status      # print the table (make up / make status do this)
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Literal

import litellm

from app.core.catalog import LLMRoutingFile, load_llm_routing
from app.core.config import get_settings

logger = logging.getLogger(__name__)

State = Literal["enabled", "degraded", "off"]
TIERS = ("bulk", "synthesis", "verify", "embedding")


@dataclass
class ModelKey:
    tier: str
    model: str
    provider: str
    key_present: bool
    env_vars: list[str]  # the env var(s) LiteLLM wants for this provider (missing ones if absent)


@dataclass
class Feature:
    name: str
    label: str
    state: State
    reason: str = ""


@dataclass
class Status:
    models: list[ModelKey]
    extra_keys: dict[str, bool]
    features: list[Feature]
    same_family_verify: bool
    warnings: list[str] = field(default_factory=list)

    def feature(self, name: str) -> Feature:
        return next(f for f in self.features if f.name == name)

    def missing_required(self) -> list[str]:
        """Env var names of the model keys that are missing (required to start)."""
        return _missing({m.tier: m for m in self.models}, *TIERS)

    def as_dict(self) -> dict:
        return asdict(self)


_ENV_HINT = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def _model_key(tier: str, model: str) -> ModelKey:
    provider = model.split("/", 1)[0]
    if provider in _ENV_HINT:  # an empty value (KEY= in .env) counts as missing
        name = _ENV_HINT[provider]
        return ModelKey(tier, model, provider, bool(os.environ.get(name, "").strip()), [name])
    try:
        v = litellm.validate_environment(model=model)
        present = bool(v.get("keys_in_environment"))
        missing = list(v.get("missing_keys") or [])
    except Exception:  # noqa: BLE001 - unknown provider: report it as missing, never crash
        present, missing = False, []
    names = missing or ([_ENV_HINT[provider]] if provider in _ENV_HINT else [])
    return ModelKey(tier, model, provider, present, names)


def _models(routing: LLMRoutingFile) -> list[ModelKey]:
    t = routing.tasks
    return [
        _model_key("bulk", t.bulk.model),
        _model_key("synthesis", t.synthesis.model),
        _model_key("verify", t.verify.model),
        _model_key("embedding", routing.embedding.model),
    ]


def _missing(models: dict[str, ModelKey], *tiers: str) -> list[str]:
    out: list[str] = []
    for tier in tiers:
        mk = models[tier]
        if not mk.key_present:
            for name in mk.env_vars or [f"{mk.provider} key"]:
                if name not in out:
                    out.append(name)
    return out


def _need(missing: list[str]) -> str:
    return "needs " + " and ".join(missing)


def _features(models: dict[str, ModelKey], extra: dict[str, bool]) -> list[Feature]:
    f: list[Feature] = []

    # Daily pipeline: bulk is required; the rest degrades it.
    if miss := _missing(models, "bulk"):
        f.append(Feature("pipeline", "Daily pipeline", "off", _need(miss)))
    else:
        gaps = []
        if miss := _missing(models, "embedding"):
            gaps.append(f"no dedup ({', '.join(miss)} missing)")
        if miss := _missing(models, "verify"):
            gaps.append(f"no second-model verification ({', '.join(miss)} missing)")
        if miss := _missing(models, "synthesis"):
            gaps.append(f"no brief / battlecards ({', '.join(miss)} missing)")
        if not extra["GITHUB_TOKEN"]:
            gaps.append("GitHub API at 60 requests/hour (no GITHUB_TOKEN)")
        state: State = "degraded" if gaps else "enabled"
        f.append(Feature("pipeline", "Daily pipeline", state, "; ".join(gaps)))

    if miss := _missing(models, "embedding"):
        f.append(Feature("embeddings", "Embeddings and dedup", "off", _need(miss)))
    else:
        f.append(Feature("embeddings", "Embeddings and dedup", "enabled"))

    if miss := _missing(models, "embedding", "synthesis", "verify"):
        f.append(Feature("ask", "Free-form Ask", "off", _need(miss)))
    else:
        f.append(Feature("ask", "Free-form Ask", "enabled"))

    if miss := _missing(models, "bulk", "synthesis", "verify", "embedding"):
        f.append(Feature("missions", "New research missions", "off", _need(miss)))
    elif not extra["TAVILY_API_KEY"]:
        f.append(
            Feature(
                "missions",
                "New research missions",
                "degraded",
                "no web search (TAVILY_API_KEY missing): knowledge base only",
            )
        )
    else:
        f.append(Feature("missions", "New research missions", "enabled"))

    f.append(Feature("analyst", "Analyst actions (review, radar)", "enabled"))
    if extra["MCP_TOKEN"]:
        f.append(Feature("mcp_http", "MCP over HTTP (/mcp)", "enabled"))
    else:
        f.append(Feature("mcp_http", "MCP over HTTP (/mcp)", "off", "needs MCP_TOKEN"))
    return f


def get_status() -> Status:
    s = get_settings()
    routing = load_llm_routing()
    models = _models(routing)
    extra = {
        "TAVILY_API_KEY": bool(s.tavily_api_key),
        "GITHUB_TOKEN": bool(s.github_token),
        "MCP_TOKEN": bool(s.mcp_token),
    }
    by_tier = {m.tier: m for m in models}
    warnings: list[str] = []
    if routing.same_family_verify:
        warnings.append(
            f"Verify uses the same model family as the writers ({routing.tasks.verify.provider}): "
            "accuracy checks are weaker (ALLOW_SAME_FAMILY_VERIFY=true)."
        )
    return Status(
        models=models,
        extra_keys=extra,
        features=_features(by_tier, extra),
        same_family_verify=routing.same_family_verify,
        warnings=warnings,
    )


def format_table(st: Status) -> str:
    lines = ["FrogRadar", "Keys (present or missing; values are never shown):"]
    for m in st.models:
        mark = "ok     " if m.key_present else "MISSING"
        hint = "" if m.key_present else f"  ({', '.join(m.env_vars)})"
        lines.append(f"  {mark} {m.tier:<10} {m.model}{hint}")
    for name, ok in st.extra_keys.items():
        lines.append(f"  {'ok     ' if ok else 'missing'} {name} (optional)")
    lines.append("Features:")
    for f in st.features:
        lines.append(f"  {f.state:<9} {f.label}" + (f" – {f.reason}" if f.reason else ""))
    lines += [f"WARNING: {w}" for w in st.warnings]
    return "\n".join(lines)


def log_status() -> None:
    # uvicorn's logger is configured in the container; the app's own INFO logs are not shown.
    out = logging.getLogger("uvicorn.error")
    try:
        out.info("\n%s", format_table(get_status()))
    except Exception as e:  # noqa: BLE001 - status is informational; never block startup
        logger.warning("status check failed: %s", e)


def main() -> None:
    print(format_table(get_status()))


if __name__ == "__main__":
    main()
