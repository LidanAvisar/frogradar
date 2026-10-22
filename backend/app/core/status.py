"""Which API keys are present and which features they enable (startup log, /api/status, CLI).

Only booleans and env var names leave this module, never key values. A missing key never crashes the
app: the feature is reported as off or degraded with a reason, and its endpoints answer with that
reason instead of failing inside a provider call.

    python -m app.core.status      # print the table (make live does this)
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Literal

import litellm

from app.core import clock
from app.core.catalog import LLMRoutingFile, load_llm_routing
from app.core.config import get_settings

logger = logging.getLogger(__name__)

State = Literal["enabled", "degraded", "off", "demo"]
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
    mode: Literal["demo", "live"]
    as_of: str | None
    models: list[ModelKey]
    extra_keys: dict[str, bool]
    features: list[Feature]
    same_family_verify: bool
    warnings: list[str] = field(default_factory=list)

    def feature(self, name: str) -> Feature:
        return next(f for f in self.features if f.name == name)

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


def _live_features(models: dict[str, ModelKey], extra: dict[str, bool]) -> list[Feature]:
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


def _demo_features(extra: dict[str, bool]) -> list[Feature]:
    return [
        Feature("pipeline", "Daily pipeline", "off", "demo mode: snapshot data, no collection"),
        Feature("embeddings", "Embeddings and dedup", "off", "demo mode"),
        Feature("ask", "Ask", "demo", "the curated demo questions, answered from the cache"),
        Feature("missions", "Research missions", "demo", "finished missions are viewable"),
        Feature("analyst", "Analyst actions (review, radar)", "enabled", "saved in the local DB"),
        Feature(
            "mcp_http",
            "MCP over HTTP (/mcp)",
            "enabled" if extra["MCP_TOKEN"] else "off",
            "" if extra["MCP_TOKEN"] else "needs MCP_TOKEN",
        ),
    ]


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
    pinned = clock.pinned_now()
    if s.demo_mode:
        features = _demo_features(extra)
        as_of = clock.snapshot_as_of()
    else:
        features = _live_features(by_tier, extra)
        as_of = None
    if s.pin_now and not s.demo_mode:
        warnings.append(f"PIN_NOW is set: the app's date is pinned to {pinned.isoformat()}.")
    return Status(
        mode="demo" if s.demo_mode else "live",
        as_of=as_of.isoformat() if as_of else None,
        models=models,
        extra_keys=extra,
        features=features,
        same_family_verify=routing.same_family_verify,
        warnings=warnings,
    )


def format_table(st: Status) -> str:
    lines = [f"FrogRadar – {st.mode} mode" + (f" (data as of {st.as_of})" if st.as_of else "")]
    if st.mode == "live":
        lines.append("Keys:")
        for m in st.models:
            mark = "ok     " if m.key_present else "MISSING"
            hint = "" if m.key_present else f"  ({', '.join(m.env_vars)})"
            lines.append(f"  {mark} {m.tier:<10} {m.model}{hint}")
        for name, ok in st.extra_keys.items():
            lines.append(f"  {'ok     ' if ok else 'missing'} {name}")
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
