"""Pydantic schemas for the per-dossier watcher endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class WatcherEntry(BaseModel):
    """One row in the per-dossier watcher list.

    The route layer joins on ``users`` to surface ``username`` for
    the avatar stack; ``user_id`` rides along so the frontend can
    deduplicate against ``actor.id`` without a second lookup.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: int
    username: str
    created_at: str


class WatchStatus(BaseModel):
    """Whether the calling actor is following ``subject_id``.

    Returned by the per-dossier ``PUT`` / ``DELETE`` so the optimistic
    UI doesn't have to round-trip to the list endpoint after toggling
    the bell — the same shape covers "I just subscribed" and "I was
    already a watcher".
    """

    model_config = ConfigDict(extra="forbid")

    watching: bool
    watcher_count: int
