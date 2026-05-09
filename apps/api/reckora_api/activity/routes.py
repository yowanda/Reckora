"""Activity-feed endpoint — read-only chronological projection.

Authorisation policy
--------------------

``GET /api/v1/subjects/{subject_id}/activity`` is gated identically to
``GET /api/v1/subjects/{subject_id}/comments``: any user with read
access to the dossier (owner, an explicit share, an assignee, or an
admin) can read it. Activity is intentionally a *read-only* surface —
events are produced as side-effects of other endpoints (post a
comment, assign a user, share, anchor at investigation time), so there
is no ``POST`` route here. That keeps the feed honest: every entry
corresponds to a row in one of the audit tables.

A reader who can no longer see a dossier (share revoked, assignment
removed) sees a 404, even if they previously authored the comment that
shows up in the feed. We trade strict comment-author access for the
simpler invariant "if you can't read the dossier, the dossier doesn't
exist for you" — which matches the rest of the collaboration surface.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from reckora.persistence.repository import SubjectRepository
from reckora_api.access.repository import AccessRepository, ActivityRow
from reckora_api.activity.schemas import ActivityEvent
from reckora_api.auth.models import Role, UserRecord
from reckora_api.auth.repository import UserRepository
from reckora_api.deps import (
    current_user,
    get_access_repo,
    get_subject_repo,
    get_user_repo,
)

router = APIRouter(prefix="/subjects/{subject_id}/activity", tags=["collab"])


def _ensure_reader(
    *,
    subject_id: str,
    actor: UserRecord,
    subject_repo: SubjectRepository,
    access_repo: AccessRepository,
) -> None:
    """Mirror of the helper in :mod:`reckora_api.collab.routes`.

    Returns 404 (not 403) when the actor cannot read the dossier so the
    API does not leak whether a subject id exists to non-readers.
    """
    if subject_repo.get(subject_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no saved dossier with id {subject_id!r}",
        )
    if actor.role is Role.ADMIN:
        return
    if not access_repo.can_read(subject_id, actor.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no saved dossier with id {subject_id!r}",
        )


class _UsernameCache:
    """Resolve ``user_id`` → username with a per-request memo.

    Same shape as the helper in :mod:`reckora_api.collab.routes`; we
    duplicate it instead of importing to keep the activity module
    free-standing (and to avoid a circular import once the collab
    module starts depending on activity for unread badges or similar).
    """

    __slots__ = ("_cache", "_repo")

    def __init__(self, repo: UserRepository) -> None:
        self._repo = repo
        self._cache: dict[int, str | None] = {}

    def __call__(self, user_id: int | None) -> str | None:
        if user_id is None:
            return None
        if user_id not in self._cache:
            record = self._repo.get_by_id(user_id)
            self._cache[user_id] = None if record is None else record.username
        return self._cache[user_id]


def _row_to_event(row: ActivityRow, resolve: _UsernameCache) -> ActivityEvent:
    return ActivityEvent(
        kind=row.kind,  # type: ignore[arg-type]  # repo emits the literal set
        actor_user_id=row.actor_user_id,
        actor_username=resolve(row.actor_user_id),
        target_user_id=row.target_user_id,
        target_username=resolve(row.target_user_id),
        excerpt=row.excerpt,
        created_at=datetime.fromisoformat(row.created_at),
    )


@router.get(
    "",
    response_model=list[ActivityEvent],
    responses={404: {"description": "subject not found or not visible to actor"}},
)
def list_activity(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ActivityEvent]:
    """Return the most-recent ``limit`` events on ``subject_id``.

    Events are ordered newest-first and cover the four observable
    mutations the platform persists: comment added, user assigned,
    user shared, dossier anchored. ``limit`` is capped at 200 to
    keep the response cheap for the dossier-detail page that
    typically only needs the top dozen entries.
    """
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    resolve = _UsernameCache(user_repo)
    return [_row_to_event(r, resolve) for r in access_repo.list_activity(subject_id, limit=limit)]


__all__: tuple[str, ...] = ("router",)
