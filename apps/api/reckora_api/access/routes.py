"""Sharing endpoints — let an owner grant other users read access.

Authorisation policy
--------------------
Only the dossier's owner (or an admin) can manage shares. We resolve the
target user by username so the API stays human-friendly; the response
echoes back the resolved numeric id so the frontend can build stable
list keys.

Sharing is read-only by design: a shared user can list the dossier and
fetch its rendered output, but they cannot delete it or re-share it.
That keeps Phase 5 scope narrow — collaborative editing is a future
revision.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from reckora.persistence.repository import SubjectRepository
from reckora_api.access.repository import AccessRepository
from reckora_api.auth.models import Role, UserRecord
from reckora_api.auth.repository import UserRepository
from reckora_api.deps import (
    current_user,
    get_access_repo,
    get_subject_repo,
    get_user_repo,
)

router = APIRouter(prefix="/subjects/{subject_id}/share", tags=["sharing"])


class ShareCreate(BaseModel):
    """Body for ``POST /api/v1/subjects/{id}/share``."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ShareEntry(BaseModel):
    """One row in the ``GET /api/v1/subjects/{id}/share`` listing."""

    model_config = ConfigDict(extra="forbid")

    user_id: int
    username: str
    created_at: datetime


def _ensure_owner_or_admin(
    *,
    subject_id: str,
    actor: UserRecord,
    subject_repo: SubjectRepository,
    access_repo: AccessRepository,
) -> int | None:
    """Resolve and authorise an owner-only mutation.

    Returns the current owner id (``None`` for legacy un-owned subjects)
    so admins can still operate on unowned dossiers without first taking
    ownership. Raises:

    - 404 when the subject does not exist (parity with non-share routes;
      avoids leaking subject existence to non-owners).
    - 403 when the actor is neither owner nor admin.
    """
    if subject_repo.get(subject_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no saved dossier with id {subject_id!r}",
        )
    owner_id = access_repo.get_owner(subject_id)
    if actor.role is Role.ADMIN:
        return owner_id
    if owner_id is None or owner_id != actor.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only the dossier owner can manage shares",
        )
    return owner_id


@router.get(
    "",
    response_model=list[ShareEntry],
    responses={
        403: {"description": "actor is not the owner / not an admin"},
        404: {"description": "subject not found"},
    },
)
def list_shares(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> list[ShareEntry]:
    """List every user who has been granted explicit access to ``subject_id``."""
    _ensure_owner_or_admin(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    out: list[ShareEntry] = []
    for user_id, created_at in access_repo.list_shares(subject_id):
        target = user_repo.get_by_id(user_id)
        if target is None:
            # Defensive: ON DELETE CASCADE should have removed this row when
            # the user was deleted. Skip silently to keep the list consistent.
            continue
        out.append(
            ShareEntry(
                user_id=user_id,
                username=target.username,
                created_at=datetime.fromisoformat(created_at),
            )
        )
    return out


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ShareEntry,
    responses={
        403: {"description": "actor is not the owner / not an admin"},
        404: {"description": "subject or target user not found"},
        409: {"description": "cannot share a dossier with its own owner"},
    },
)
def create_share(
    subject_id: str,
    payload: ShareCreate,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> ShareEntry:
    """Grant ``payload.username`` read access to ``subject_id``.

    Idempotent: re-sharing with an already-shared user is a no-op and
    returns the existing entry. We use ``201 Created`` for both new and
    existing shares because the endpoint guarantees the share exists by
    the time it returns; clients that need to distinguish can compare
    ``created_at``.
    """
    owner_id = _ensure_owner_or_admin(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    target = user_repo.get_by_username(payload.username)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no user named {payload.username!r}",
        )
    if owner_id is not None and target.id == owner_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot share a dossier with its own owner",
        )
    ts = datetime.now(UTC).isoformat()
    access_repo.add_share(subject_id, target.id, created_at=ts)
    # Re-read so we report the existing timestamp on idempotent calls.
    for user_id, created_at in access_repo.list_shares(subject_id):
        if user_id == target.id:
            return ShareEntry(
                user_id=target.id,
                username=target.username,
                created_at=datetime.fromisoformat(created_at),
            )
    # Should never reach here, but fall back to the timestamp we just inserted.
    return ShareEntry(  # pragma: no cover - belt-and-braces
        user_id=target.id,
        username=target.username,
        created_at=datetime.fromisoformat(ts),
    )


@router.delete(
    "/{username}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        403: {"description": "actor is not the owner / not an admin"},
        404: {"description": "subject, user, or share not found"},
    },
)
def revoke_share(
    subject_id: str,
    username: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> None:
    """Revoke ``username``'s access to ``subject_id``.

    Returns 204 on success, 404 if any of subject / user / share is
    missing. We deliberately do not distinguish between the three not-
    found cases in the response body; the actor only needs to know "this
    share does not currently exist".
    """
    _ensure_owner_or_admin(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    target = user_repo.get_by_username(username)
    if target is None or not access_repo.remove_share(subject_id, target.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no share for user {username!r} on subject {subject_id!r}",
        )
    return None
