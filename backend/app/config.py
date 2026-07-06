"""Application configuration.

No secrets in config files (PRD 12). The OpenRouter key lives in the encrypted
secret store (app/services/secrets.py), not here. This module holds only
non-secret operational settings, sourced from environment variables so the same
image runs on Unraid or Azure Container Apps. (Price-sheet sync reads its own
env vars in app/pricesync/config.py.)
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
    # read-only/empty and AI assist is disabled gracefully.
    master_secret: str | None = None

    # CORS origins for the React dev/prod front end.
    cors_origins: str = "*"

    # OpenRouter model used for coverage suggestions (key comes from secret store).
    openrouter_model: str = "anthropic/claude-3.5-sonnet"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Default market/currency (PRD 14: US/USD assumed).
    default_market: str = "US"
    default_currency: str = "USD"

    # Build provenance, baked into the image at publish time (Dockerfile ARG ->
    # ENV). Empty on local/dev builds, which suppresses the update check.
    build_sha: str = ""       # full git sha of the running image
    build_version: str = ""   # semver like "1.2.3" when built from a v* tag, else ""
    build_ref: str = ""       # the git ref built (e.g. "refs/tags/v1.2.3" or a branch)

    # Repo the update check queries (owner/name). Matches the ghcr image slug the
    # publish workflow uses. Overridable so a fork points at its own repo.
    update_repo: str = "jmorganthall/m365tco"
    # How long to cache the "latest" lookup, in seconds (default 6h).
    update_check_ttl_seconds: int = 21600


settings = Settings()
