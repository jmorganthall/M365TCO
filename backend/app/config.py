"""Application configuration.

No secrets in config files (PRD 12). The OpenRouter key and Partner Center
refresh token live in the encrypted secret store (app/services/secrets.py),
not here. This module holds only non-secret operational settings, sourced from
environment variables so the same image runs on Unraid or Azure Container Apps.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TCO_", env_file=".env", extra="ignore")

    # Persistence. SQLite for v1 single-container; swap to Postgres via this URL
    # only (SQLAlchemy keeps it a configuration change, not a code change).
    database_url: str = "sqlite:////data/tco.db"

    # Directory for the encrypted secret store and any local artifacts.
    data_dir: str = "/data"

    # Master secret that unlocks the local encrypted secret store. Operator
    # supplied (env var or Docker/Unraid secret). If unset, the secret store is
    # read-only/empty and AI + Partner Center features are disabled gracefully.
    master_secret: str | None = None

    # CORS origins for the React dev/prod front end.
    cors_origins: str = "*"

    # OpenRouter model used for coverage suggestions (key comes from secret store).
    openrouter_model: str = "anthropic/claude-3.5-sonnet"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Default market/currency (PRD 14: US/USD assumed).
    default_market: str = "US"
    default_currency: str = "USD"


settings = Settings()
