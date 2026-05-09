"""HTTP surface for per-dossier status."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from reckora.persistence.repository import SubjectRepository
from reckora_api.access.repository import AccessRepository, StatusRow
from reckora_api.auth.models import Role, UserRecord
from reckora_api.auth.repository import UserRepository
from reckora_api.deps import (
    current_user,
    get_access_repo,
    get_subject_repo,
    get_user_repo,
)
from reckora_api.dossier_status.schemas import (
    ALLOWED_STATUSES,
    DEFAULT_STATUS,
    StatusEntry,
    StatusUpdate,
)

status_router = APIRouter(prefix="/subjects/{subject_id}/status", tags=["status"])
status_catalog_router = APIRouter(prefix="/status", tags=["status"])


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
    """Reader-tier access (or admin), 404-on-deny to avoid existence leak."""
    _ensure_subject_exists(subject_id, subject_repo)
    if actor.role is Role.ADMIN:
        return
    if not access_repo.can_read(subject_id, actor.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no saved dossier with id {subject_id!r}",
        )


def _ensure_owner_or_admin(
    *,
    subject_id: str,
    actor: UserRecord,
    subject_repo: SubjectRepository,
    access_repo: AccessRepository,
) -> None:
    """Authorise status mutations.

    Owner-or-admin only, mirroring assignment / label management.
    Readers without ownership get 403 so the UI can grey out the
    state-machine controls; outsiders get 404 to avoid leaking
    subject existence.
    """
    _ensure_subject_exists(subject_id, subject_repo)
    owner_id = access_repo.get_owner(subject_id)
    if actor.role is Role.ADMIN:
        return
    if owner_id is not None and owner_id == actor.id:
        return
    if access_repo.can_read(subject_id, actor.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only the dossier owner or an admin can change status",
        )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"no saved dossier with id {subject_id!r}",
    )


def _row_to_entry(row: StatusRow | None, user_repo: UserRepository) -> StatusEntry:
    """Project a (possibly absent) row into the wire shape.

    Implicit default: when ``row is None`` we report ``open`` with
    no audit metadata, matching the "never moved off the default"
    interpretation of ``AccessRepository.get_status``.
    """
    if row is None:
        return StatusEntry(
            status=DEFAULT_STATUS,
            updated_by=None,
            updated_at=None,
        )
    updated_by = None
    if row.updated_by is not None:
        user = user_repo.get_by_id(row.updated_by)
        updated_by = user.username if user is not None else None
    return StatusEntry(
        status=row.status,
        updated_by=updated_by,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# Per-dossier endpoints
# ---------------------------------------------------------------------------


@status_router.get("", response_model=StatusEntry)
def get_dossier_status(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> StatusEntry:
    """Read the current status of a dossier (any reader)."""
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    return _row_to_entry(access_repo.get_status(subject_id), user_repo)


@status_router.put(
    "",
    response_model=StatusEntry,
    responses={
        403: {"description": "actor is not the dossier owner or an admin"},
        404: {"description": "subject not found, or actor is not a reader"},
        422: {"description": "status is not in the allow-list"},
    },
)
def update_dossier_status(
    subject_id: str,
    payload: StatusUpdate,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> StatusEntry:
    """Move a dossier to ``open``, ``on_hold``, or ``closed``.

    Idempotent: writing the same status twice updates ``updated_at``
    but otherwise leaves the surface unchanged. We deliberately
    materialise a row even on transitions to ``open`` so the audit
    trail of ping-pong transitions survives.
    """
    _ensure_owner_or_admin(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    if payload.status not in ALLOWED_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=(f"status must be one of {sorted(ALLOWED_STATUSES)}; got {payload.status!r}"),
        )
    row = access_repo.set_status(
        subject_id,
        payload.status,
        updated_by=actor.id,
        updated_at=datetime.now(UTC).isoformat(),
    )
    return _row_to_entry(row, user_repo)


# ---------------------------------------------------------------------------
# Global counts
# ---------------------------------------------------------------------------


@status_catalog_router.get("", response_model=dict[str, int])
def status_counts(
    actor: Annotated[UserRecord, Depends(current_user)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> dict[str, int]:
    """Return ``{status: count}`` for dossiers visible to the actor.

    Powers the sidebar's status-bucket headers. Buckets that have
    zero visible dossiers are omitted from the response — the
    frontend can render the canonical set itself if it wants to
    show ``Closed (0)`` zero-states.
    """
    return access_repo.status_counts(actor.id, is_admin=actor.role is Role.ADMIN)
