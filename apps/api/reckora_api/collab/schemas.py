"""Pydantic schemas for the collaboration endpoints (comments + assignees)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# We cap the body at 10_000 characters because every comment is fetched in
# bulk on the dossier view; bigger payloads belong in evidence files, not in
# the comment thread.
_COMMENT_BODY_MAX = 10_000


class CommentCreate(BaseModel):
    """Body for ``POST /api/v1/subjects/{subject_id}/comments``."""

    model_config = ConfigDict(extra="forbid")

    body: str = Field(..., min_length=1, max_length=_COMMENT_BODY_MAX)


class CommentUpdate(BaseModel):
    """Body for ``PATCH /api/v1/subjects/{subject_id}/comments/{id}``.

    Same shape as :class:`CommentCreate` — we don't allow partial-field
    PATCHes because there is currently only one editable column. If
    additional fields land later (e.g. ``pinned``), they should be
    added here as ``Optional`` rather than a separate route.
    """

    model_config = ConfigDict(extra="forbid")

    body: str = Field(..., min_length=1, max_length=_COMMENT_BODY_MAX)


class CommentEntry(BaseModel):
    """One row in ``GET /api/v1/subjects/{subject_id}/comments``.

    ``author_username`` is ``None`` for comments whose author has been
    deleted from the user table — the row is preserved (not cascade-
    deleted) so the thread keeps its narrative continuity, but the
    frontend can render the row as "deleted user".

    ``mentions`` carries the list of resolved usernames from any
    ``@username`` tokens in the body — deduped, sorted alphabetically
    for stable rendering, and limited to users who could read the
    dossier when the comment fired. Unresolved tokens (typos, foreign
    handles) are dropped silently rather than surfaced.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    author_user_id: int
    author_username: str | None
    body: str
    created_at: datetime
    updated_at: datetime | None = None
    mentions: list[str] = []


class AssigneeCreate(BaseModel):
    """Body for ``POST /api/v1/subjects/{subject_id}/assignees``."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class AssigneeEntry(BaseModel):
    """One row in ``GET /api/v1/subjects/{subject_id}/assignees``.

    ``assigned_by_username`` is ``None`` when the granting user has been
    deleted (``ON DELETE SET NULL`` on the column) or the assignment
    was issued by a non-human caller (e.g. an auto-triage worker).
    """

    model_config = ConfigDict(extra="forbid")

    user_id: int
    username: str
    assigned_by_user_id: int | None = None
    assigned_by_username: str | None = None
    assigned_at: datetime
