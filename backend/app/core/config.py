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

    @field_validator("config_dir", mode="before")
    @classmethod
    def _empty_config_dir_means_default(cls, v: object) -> object:
        return _REPO_ROOT / "config" if v in ("", None) else v


@lru_cache
def get_settings() -> Settings:
    return Settings()
