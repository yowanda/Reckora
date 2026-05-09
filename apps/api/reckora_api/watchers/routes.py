"""HTTP surface for per-dossier watchers / following."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from reckora.persistence.repository import SavedDossierSummary, SubjectRepository
from reckora_api.access.repository import AccessRepository
from reckora_api.auth.models import Role, UserRecord
from reckora_api.auth.repository import UserRepository
from reckora_api.deps import (
    current_user,
    get_access_repo,
    get_subject_repo,
    get_user_repo,
)
from reckora_api.watchers.schemas import WatcherEntry, WatchStatus

watchers_router = APIRouter(prefix="/subjects/{subject_id}/watchers", tags=["watchers"])
me_watching_router = APIRouter(prefix="/me/watching", tags=["watchers"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_subject_exists(subject_id: str, subject_repo: SubjectRepository) -> None:
    if subject_repo.get(subject_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no saved dossier with id {subject_id!r}",
        )


def _ensure_reader(
    *,
    subject_id: str,
    actor: UserRecord,
    subject_repo: SubjectRepository,
    access_repo: AccessRepository,
) -> None:
    """Reader-tier access (or admin), 404-on-deny to avoid existence leak.

    Watchers mirror the comments / reactions / labels read gate: any
    reader of the dossier may list watchers and toggle their own
    subscription. There is deliberately no ``role >= editor`` tier
    for "subscribe other users" — that would be a notification
    primitive (``mention`` / ``request review``), not a watch.
    """
    _ensure_subject_exists(subject_id, subject_repo)
    if actor.role is Role.ADMIN:
        return
    if not access_repo.can_read(subject_id, actor.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no saved dossier with id {subject_id!r}",
        )


# ---------------------------------------------------------------------------
# Per-dossier endpoints
# ---------------------------------------------------------------------------


@watchers_router.get("", response_model=list[WatcherEntry])
def list_watchers(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> list[WatcherEntry]:
    """Return every watcher of ``subject_id``, oldest subscription first.

    Visibility mirrors the dossier itself: any reader (owner / sharer
    / assignee / admin) sees the full list; outsiders 404.
    """
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    rows = access_repo.list_watchers(subject_id)
    out: list[WatcherEntry] = []
    for row in rows:
        user = user_repo.get_by_id(row.user_id)
        if user is None:
            # ON DELETE CASCADE should have wiped this row already, but
            # we defend in depth: skip rather than 500 on a stale row.
            continue
        out.append(
            WatcherEntry(
                user_id=row.user_id,
                username=user.username,
                created_at=row.created_at,
            )
        )
    return out


@watchers_router.put(
    "/me",
    response_model=WatchStatus,
    responses={
        404: {"description": "subject not found, or actor is not a reader"},
    },
)
def subscribe_to_dossier(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> WatchStatus:
    """Start watching ``subject_id`` (idempotent).

    Returns the post-call ``watcher_count`` so the optimistic UI can
    update the badge without a second round-trip. Re-subscribing while
    already a watcher is a no-op success — the route layer treats both
    "first click" and "second click" the same way.
    """
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    access_repo.add_watcher(
        subject_id,
        actor.id,
        created_at=datetime.now(UTC).isoformat(),
    )
    return WatchStatus(
        watching=True,
        watcher_count=len(access_repo.list_watchers(subject_id)),
    )


@watchers_router.delete(
    "/me",
    response_model=WatchStatus,
    responses={
        404: {"description": "subject not found, or actor is not a reader"},
    },
)
def unsubscribe_from_dossier(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> WatchStatus:
    """Stop watching ``subject_id`` (idempotent).

    Unsubscribing while not currently a watcher is *also* a no-op
    success rather than 404 — the bell is a binary toggle from the
    UI's perspective and a stale optimistic state shouldn't break a
    user trying to clean up their watch list.
    """
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    access_repo.remove_watcher(subject_id, actor.id)
    return WatchStatus(
        watching=False,
        watcher_count=len(access_repo.list_watchers(subject_id)),
    )


# ---------------------------------------------------------------------------
# Per-actor "my watch list"
# ---------------------------------------------------------------------------


@me_watching_router.get("", response_model=list[SavedDossierSummary])
def list_my_watched(
    actor: Annotated[UserRecord, Depends(current_user)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    limit: int = 50,
) -> list[SavedDossierSummary]:
    """Return the calling actor's watch list, most-recently-followed first.

    The shape matches :class:`SavedDossierSummary` so the frontend
    can render this list with the same row component as ``Recent`` /
    ``Shared with me``. ``limit`` defaults to 50 — large enough to
    cover a power user's saved cases, small enough to keep the
    response under a kilobyte.
    """
    if limit < 0:
        raise HTTPException(
            status_code=422,
            detail="limit must be non-negative",
        )
    return access_repo.list_watched_summaries(actor.id, limit=limit)
