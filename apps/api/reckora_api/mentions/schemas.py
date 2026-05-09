"""Pydantic schemas for the mentions endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MentionEntry(BaseModel):
    """One row in ``GET /api/v1/me/mentions``.

    Carries enough denormalised context for the FE to render the
    mention card without a second round-trip:

    * the comment body (so the user sees the surrounding sentence),
    * the dossier id (so the card can link to the right thread),
    * the author + their username (to avoid an N+1 user lookup),
    * both the comment timestamp and the mention timestamp (they
      diverge if a mention is added on edit, when that surface is
      eventually wired in).
    """

    model_config = ConfigDict(extra="forbid")

    comment_id: int
    subject_id: str
    author_user_id: int
    author_username: str | None
    body: str
    comment_created_at: datetime
    mention_created_at: datetime
