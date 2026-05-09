"""HTTP surface for per-actor dossier visit stamps + unread counts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from reckora.persistence.repository import SubjectRepository
from reckora_api.access.repository import AccessRepository
from reckora_api.auth.models import UserRecord
from reckora_api.deps import current_user, get_access_repo, get_subject_repo
from reckora_api.visits.schemas import UnreadStatus, VisitEntry

visits_router = APIRouter(prefix="/subjects/{subject_id}", tags=["visits"])


def _ensure_reader(
    *,
    subject_id: str,
    actor: UserRecord,
    subject_repo: SubjectRepository,
    access_repo: AccessRepository,
) -> None:
    """Reject the caller with 404 if they cannot read ``subject_id``.

    We collapse unknown-subject and no-access into the same 404 so
    a non-reader cannot probe for the existence of a dossier they
    are not allowed to see.
    """
    if subject_repo.get(subject_id) is None:
        raise HTTPException(status_code=404, detail="subject not found")
    if not access_repo.can_read(subject_id, actor.id):
        raise HTTPException(status_code=404, detail="subject not found")


@visits_router.post(
    "/visits/me",
    response_model=VisitEntry,
    responses={404: {"description": "subject not found or not visible to actor"}},
)
def mark_visited(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> VisitEntry:
    """Advance the caller's last-seen stamp on ``subject_id`` to *now*."""
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    now = datetime.now(UTC).isoformat()
    stamp = access_repo.mark_visited(subject_id, actor.id, now=now)
    return VisitEntry(
        subject_id=subject_id,
        user_id=actor.id,
        last_seen_at=datetime.fromisoformat(stamp),
    )


@visits_router.get(
    "/visits/me",
    response_model=VisitEntry,
    responses={
        404: {"description": "subject not found, not visible, or never visited"},
    },
)
def get_my_visit(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> VisitEntry:
    """Return the caller's last-seen stamp on ``subject_id``."""
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    stamp = access_repo.get_last_visit(subject_id, actor.id)
    if stamp is None:
        raise HTTPException(status_code=404, detail="never visited")
    return VisitEntry(
        subject_id=subject_id,
        user_id=actor.id,
        last_seen_at=datetime.fromisoformat(stamp),
    )


@visits_router.get(
    "/unread",
    response_model=UnreadStatus,
    responses={404: {"description": "subject not found or not visible to actor"}},
)
def get_unread_status(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> UnreadStatus:
    """Return the caller's unread-comment count + last-seen stamp."""
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    stamp = access_repo.get_last_visit(subject_id, actor.id)
    count = access_repo.count_unread_comments(subject_id, actor.id)
    return UnreadStatus(
        subject_id=subject_id,
        user_id=actor.id,
        last_seen_at=datetime.fromisoformat(stamp) if stamp is not None else None,
        unread_comment_count=count,
    )
