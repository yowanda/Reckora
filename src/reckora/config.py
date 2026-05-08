"""Runtime configuration via environment variables / `.env`.

We keep the key names matching ecosystem conventions where they exist
(`OPENAI_API_KEY`, `GITHUB_TOKEN`) and prefix Reckora-specific ones with
`RECKORA_`. Pydantic-settings reads `.env` in the working directory.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration, populated from env vars and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_model: str = Field(
        default="gpt-4o-mini",
        validation_alias="RECKORA_OPENAI_MODEL",
    )
    github_token: str | None = Field(default=None, validation_alias="GITHUB_TOKEN")
    user_agent: str = Field(
        default="Reckora/0.1",
        validation_alias="RECKORA_USER_AGENT",
    )
    db_path: str = Field(
        default="reckora.db",
        validation_alias="RECKORA_DB_PATH",
    )
    # Have I Been Pwned (https://haveibeenpwned.com/API/v3) API key. The
    # breach lookup collector is opt-in (CLI ``--breach`` flag, API
    # ``breach: true``) AND requires this key; without it the collector
    # gracefully returns no traces so investigations stay deterministic
    # even on hosts that have never seen the key.
    hibp_api_key: str | None = Field(default=None, validation_alias="HIBP_API_KEY")
    # Etherscan (https://docs.etherscan.io/) API key. The Ethereum wallet
    # collector works without one (anonymous tier, ~5 req/sec / IP), but
    # passing a key lifts the rate limit and is the recommended setup for
    # production / batch investigations. Unlike HIBP this is *not* a
    # feature flag — the collector is always wired into the orchestrator
    # and the key is purely a rate-limit lever.
    etherscan_api_key: str | None = Field(default=None, validation_alias="ETHERSCAN_API_KEY")


settings = Settings()
