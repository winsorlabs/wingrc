"""Runtime configuration, read from the environment (12-factor)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WINGRC_", env_file=".env")

    # Default points at the docker-compose Postgres service.
    database_url: str = "postgresql+psycopg://wingrc:wingrc@localhost:5432/wingrc"
    app_name: str = "WinGRC"
    environment: str = "development"

    # AI provider abstraction — pluggable so CUI-sensitive tenants can keep
    # generation local. Not exercised by the scope module yet.
    ai_provider: str = "none"  # one of: none | anthropic | azure_openai | local


@lru_cache
def get_settings() -> Settings:
    return Settings()
