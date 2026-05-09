"""Pydantic schemas for the per-actor notes endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Notes are scratch-pad markdown, but they live in SQLite TEXT
# columns alongside everything else. 16 KiB is generous for a
# personal note and small enough that pathological clients can't
# blow the database up by spamming PUTs.
_MAX_BODY_BYTES = 16 * 1024


class NoteUpsert(BaseModel):
    """Body for ``PUT /api/v1/subjects/{id}/notes/me``."""

    model_config = ConfigDict(extra="forbid")

    body: str = Field(..., min_length=1, max_length=_MAX_BODY_BYTES)


class NoteEntry(BaseModel):
    """Wire shape for a per-actor note.

    ``user_id`` is included so a future surface that lets admins
    browse other actors' notes (with explicit consent / disclosure)
    can re-use this schema without a wire-shape change. Today the
    only producer is the calling actor's own ``GET /notes/me``,
    where ``user_id`` always matches the bearer.
    """

    model_config = ConfigDict(extra="forbid")

    subject_id: str
    user_id: int
    body: str
    created_at: datetime
    updated_at: datetime
