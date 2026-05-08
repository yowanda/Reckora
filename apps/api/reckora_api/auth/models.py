"""Pydantic models for the auth surface.

Public-facing models live here so request/response schemas show up in the
OpenAPI document. The internal :class:`UserRecord` carries the password hash
and is never serialised to clients.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    """Coarse-grained authorisation role attached to every user.

    ``viewer`` is the default for self-service registrations: users see only
    the dossiers they own (or that have been explicitly shared with them) and
    cannot manage other users. ``admin`` is reserved for operators bootstrapped
    via the ``reckora-api`` CLI; admins can list / fetch / delete any saved
    dossier and promote / demote other users.
    """

    ADMIN = "admin"
    VIEWER = "viewer"


class UserCreate(BaseModel):
    """Body for ``POST /api/v1/auth/register``."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(..., min_length=8, max_length=200)


class UserPublic(BaseModel):
    """Anything the API ever returns about a user."""

    model_config = ConfigDict(extra="forbid")

    id: int
    username: str
    created_at: datetime
    role: Role


class TokenResponse(BaseModel):
    """OAuth2-shaped response for ``POST /api/v1/auth/token``."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class RoleUpdate(BaseModel):
    """Body for ``PATCH /api/v1/users/{user_id}/role`` (admin only)."""

    model_config = ConfigDict(extra="forbid")

    role: Role


class UserRecord(BaseModel):
    """Internal user row; never returned to clients."""

    model_config = ConfigDict(frozen=True)

    id: int
    username: str
    password_hash: str
    created_at: datetime
    is_active: bool
    role: Role
