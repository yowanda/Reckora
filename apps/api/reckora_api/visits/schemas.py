"""Pydantic schemas for the per-actor visit stamps endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class VisitEntry(BaseModel):
    """Wire shape for a per-actor dossier visit stamp."""

    model_config = ConfigDict(extra="forbid")

    subject_id: str
    user_id: int
    last_seen_at: datetime


class UnreadStatus(BaseModel):
    """Wire shape for ``GET /subjects/{id}/unread``.

    ``last_seen_at`` is ``None`` until the actor visits for the
    first time. While it is ``None``, ``unread_comment_count`` is
    the **total** comment count on the dossier so a freshly-shared
    collaborator gets a non-zero badge that motivates them to open
    the dossier.
    """

    model_config = ConfigDict(extra="forbid")

    subject_id: str
    user_id: int
    last_seen_at: datetime | None
    unread_comment_count: int
