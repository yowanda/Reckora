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
    # Model used when the reasoning layer is driven by ChatGPT OAuth
    # (``reckora auth login``) instead of an OpenAI Platform API key.
    # The ChatGPT Codex backend hosts a different model lineup
    # (``gpt-5.5`` / ``gpt-5.4`` / ``gpt-5.3-codex`` / …) than
    # ``api.openai.com``, so we keep this knob distinct from
    # ``RECKORA_OPENAI_MODEL`` to avoid 4xx-ing requests against the
    # API-key path with a Codex-only model name. ``gpt-5.5`` is the
    # current Codex default for ChatGPT Plus accounts (older
    # ``gpt-5.1-codex-mini`` was retired from the Plus tier).
    openai_oauth_model: str = Field(
        default="gpt-5.5",
        validation_alias="RECKORA_OPENAI_OAUTH_MODEL",
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
    # AgentRouter (https://agentrouter.org) is a third-party LLM gateway that
    # exposes OpenAI-, Anthropic-, and Gemini-compatible endpoints behind a
    # single bearer token. We talk to it via the OpenAI-compatible
    # ``/v1/chat/completions`` shape so the existing ``AsyncOpenAI`` client
    # can be reused; only the base URL and credential change.
    #
    # ``agentrouter_api_key`` is the *system-level* fallback for users who
    # have not set their own AgentRouter key on their profile (BYOK). When
    # both are unset the AgentRouter path raises with a helpful message.
    agentrouter_api_key: str | None = Field(
        default=None,
        validation_alias="AGENTROUTER_API_KEY",
    )
    agentrouter_base_url: str = Field(
        default="https://agentrouter.org/v1",
        validation_alias="RECKORA_AGENTROUTER_BASE_URL",
    )
    # Hardcoded default uses Anthropic's Claude Opus 4.6 alias as exposed by
    # AgentRouter's console (see /console/token model picker). Override via
    # ``RECKORA_AGENTROUTER_MODEL`` if a different upstream model is needed.
    agentrouter_model: str = Field(
        default="claude-opus-4-6",
        validation_alias="RECKORA_AGENTROUTER_MODEL",
    )


settings = Settings()
