"""Settings for the Reckora HTTP API.

Kept separate from :mod:`reckora.config` so the CLI does not have to think
about JWT secrets or CORS origins when running in offline mode. The two
settings instances coexist — both are read from the same ``.env`` file.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class APISettings(BaseSettings):
    """Process-wide configuration for ``reckora.api``.

    Environment variables are prefixed with ``RECKORA_API_`` except for the
    shared ``RECKORA_DB_PATH`` which the CLI also reads — both halves of the
    product point at the same SQLite file by default.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    jwt_secret: str = Field(default="", validation_alias="RECKORA_API_JWT_SECRET")
    jwt_algorithm: str = Field(
        default="HS256",
        validation_alias="RECKORA_API_JWT_ALGORITHM",
    )
    jwt_ttl_seconds: int = Field(
        default=3600,
        validation_alias="RECKORA_API_JWT_TTL_SECONDS",
    )
    cors_origins_raw: str = Field(
        default="http://localhost:5173",
        validation_alias="RECKORA_API_CORS_ORIGINS",
    )
    db_path: str = Field(default="reckora.db", validation_alias="RECKORA_DB_PATH")
    docs_enabled: bool = Field(
        default=True,
        validation_alias="RECKORA_API_DOCS_ENABLED",
    )
    screenshots_dir: str = Field(
        default="screenshots",
        validation_alias="RECKORA_API_SCREENSHOTS_DIR",
    )
    screenshots_url_prefix: str = Field(
        default="/screenshots",
        validation_alias="RECKORA_API_SCREENSHOTS_URL_PREFIX",
    )
    # Filesystem path holding the Fernet symmetric key used to
    # encrypt per-user secrets at rest (today: AgentRouter API
    # keys). When unset we co-locate the key with the SQLite file
    # so a vanilla single-host deployment Just Works — the file is
    # auto-generated on first start and persists across restarts.
    # Operators should back this file up alongside the database;
    # losing it makes every saved BYOK key unrecoverable (which is
    # the desired property if the host is compromised).
    fernet_key_path: str = Field(
        default="",
        validation_alias="RECKORA_API_FERNET_KEY_PATH",
    )

    # ---- OAuth (social login) ----
    # When ``RECKORA_API_OAUTH_GITHUB_CLIENT_ID`` is empty the OAuth
    # routes return 503; deployments that don't want social login can
    # leave every OAuth setting unset and the rest of the API still
    # works.
    oauth_github_client_id: str = Field(
        default="",
        validation_alias="RECKORA_API_OAUTH_GITHUB_CLIENT_ID",
    )
    oauth_github_client_secret: str = Field(
        default="",
        validation_alias="RECKORA_API_OAUTH_GITHUB_CLIENT_SECRET",
    )
    # The redirect URI registered with the GitHub OAuth App. Must
    # point at ``<api>/api/v1/auth/oauth/github/callback`` — anything
    # else will be rejected by GitHub at the authorize step.
    oauth_github_redirect_url: str = Field(
        default="http://localhost:8000/api/v1/auth/oauth/github/callback",
        validation_alias="RECKORA_API_OAUTH_GITHUB_REDIRECT_URL",
    )
    # Base URL the OAuth callback redirects the browser to once a JWT
    # has been minted. We hand off the token via the URL fragment so
    # it never appears in the API's access log. Defaults to the Vite
    # dev server origin for local development.
    frontend_url: str = Field(
        default="http://localhost:5173",
        validation_alias="RECKORA_API_FRONTEND_URL",
    )

    @property
    def cors_origins(self) -> list[str]:
        """Parse the comma-separated origin list once."""
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]

    @property
    def github_oauth_enabled(self) -> bool:
        """``True`` iff both client id and secret are configured."""
        return bool(self.oauth_github_client_id and self.oauth_github_client_secret)
