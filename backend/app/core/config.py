from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings read from environment variables (and a local .env, if present)."""

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    app_name: str = "FrogRadar"
    database_url: str = "postgresql+psycopg://frogradar:change-me@localhost:5432/frogradar"


@lru_cache
def get_settings() -> Settings:
    return Settings()
