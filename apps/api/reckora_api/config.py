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

    @property
    def cors_origins(self) -> list[str]:
        """Parse the comma-separated origin list once."""
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]
