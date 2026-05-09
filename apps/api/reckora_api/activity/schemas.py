"""Pydantic schemas for the per-dossier activity-feed endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ActivityKind = Literal["comment_added", "assigned", "shared", "anchored"]


class ActivityEvent(BaseModel):
    """One row in ``GET /api/v1/subjects/{subject_id}/activity``.

    The feed is a *projection* over the underlying tables — it does not
    introduce new state. As a result, every column is nullable except
    ``kind`` and ``created_at`` so the schema accommodates events that
    don't carry an actor (``shared`` / ``anchored``), don't carry a
    target (``comment_added`` / ``anchored``), or don't carry a body
    excerpt (anything except ``comment_added``).

    Username fields are populated server-side from the user table so
    clients can render ``"@bob assigned @carol"`` strings without a
    second round-trip per event. They drop to ``None`` when the
    underlying user has been deleted but the audit row survived
    (``ON DELETE SET NULL`` for ``assigned_by``, plain dangling row in
    edge cases).
    """

    model_config = ConfigDict(extra="forbid")

    kind: ActivityKind
    actor_user_id: int | None = None
    actor_username: str | None = None
    target_user_id: int | None = None
    target_username: str | None = None
    excerpt: str | None = None
    created_at: datetime
