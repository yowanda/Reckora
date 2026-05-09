"""Pydantic schemas for the per-actor TODO endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# 512 chars is generous for a checklist item ("re-run the avatar
# OSINT on the new handle" is ~50 chars). Anything longer almost
# certainly belongs in a note or a comment, which have their own
# 16 KiB limits.
_MAX_BODY_CHARS = 512


class TodoCreate(BaseModel):
    """Body for ``POST /subjects/{id}/todos/me``."""

    model_config = ConfigDict(extra="forbid")

    body: str = Field(..., min_length=1, max_length=_MAX_BODY_CHARS)


class TodoUpdate(BaseModel):
    """Body for ``PATCH /subjects/{id}/todos/me/{todo_id}``.

    Both fields are optional; sending neither is a 422-triggering
    no-op so a buggy FE doesn't accidentally bump ``updated_at``
    with an empty payload.
    """

    model_config = ConfigDict(extra="forbid")

    body: str | None = Field(default=None, min_length=1, max_length=_MAX_BODY_CHARS)
    done: bool | None = None


class TodoEntry(BaseModel):
    """Wire shape for a single TODO row.

    ``user_id`` is included so a future admin-style cross-actor
    surface (audit, diagnostics) can re-use the schema unchanged.
    Today the only producer is the calling actor's own listing,
    where ``user_id`` always matches the bearer.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    subject_id: str
    user_id: int
    body: str
    done: bool
    created_at: datetime
    updated_at: datetime
