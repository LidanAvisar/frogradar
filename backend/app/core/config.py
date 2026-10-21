from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> repo root is three levels above app/
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """App settings read from environment variables (and a local .env, if present)."""

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    app_name: str = "FrogRadar"
    database_url: str = "postgresql+psycopg://frogradar:change-me@localhost:5432/frogradar"
    # Directory with competitors.yaml, sources.yaml, capabilities.yaml, llm_routing.yaml.
    config_dir: Path = _REPO_ROOT / "config"

    # Optional overrides for config/llm_routing.yaml (empty = use the YAML value).
    llm_model_bulk: str = ""
    llm_model_synthesis: str = ""
    llm_model_verify: str = ""
    embedding_model: str = ""

    # LLM gateway (app/core/llm).
    demo_mode: bool = False  # serve LLM calls from llm_cache only; never call a provider
    llm_daily_budget_usd: float = 2.0  # BudgetExceeded once today's (UTC) spend reaches this
    llm_timeout_s: float = 60.0  # per provider request
    llm_max_retries: int = 3  # retries on rate limits / 5xx / timeouts (not on 4xx)
    llm_backoff_base_s: float = 1.0  # first backoff delay; doubles per retry (capped at 30s)

    # Collectors (app/pipeline/collectors).
    github_token: str = ""  # optional: 5000 instead of 60 GitHub API requests/hour
    collector_user_agent: str = (
        "FrogRadarBot/0.1 (+https://github.com/LidanAvisar/frogradar; competitive-intel research)"
    )
    collector_timeout_s: float = 20.0
    collector_domain_delay_s: float = 1.0  # minimum gap between requests to the same host

    # Research missions (S15): Tavily web search; empty = web search skipped (knowledge base only).
    tavily_api_key: str = ""
    # In-process mission worker thread, started with the API (off in tests / CLI tools).
    mission_worker: bool = True

    # Scheduler service (app/scheduler.py): standard 5-field cron, evaluated in UTC.
    daily_run_cron: str = "0 6 * * *"

    @field_validator("config_dir", mode="before")
    @classmethod
    def _empty_config_dir_means_default(cls, v: object) -> object:
        return _REPO_ROOT / "config" if v in ("", None) else v


@lru_cache
def get_settings() -> Settings:
    return Settings()
