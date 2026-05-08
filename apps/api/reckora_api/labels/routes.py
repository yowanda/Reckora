"""HTTP surface for per-dossier labels.

Two routers are exposed (so the global catalog can mount at
``/api/v1/labels`` rather than nested under a subject):

``labels_router``         → ``/subjects/{sid}/labels...``
``labels_catalog_router`` → ``/labels``
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from reckora.persistence.repository import SubjectRepository
from reckora_api.access.repository import AccessRepository, LabelRow
from reckora_api.auth.models import Role, UserRecord
from reckora_api.auth.repository import UserRepository
from reckora_api.deps import (
    current_user,
    get_access_repo,
    get_subject_repo,
    get_user_repo,
)
from reckora_api.labels.schemas import (
    LABEL_MAX_LENGTH,
    LABEL_PATTERN,
    LabelCatalogEntry,
    LabelEntry,
)

labels_router = APIRouter(prefix="/subjects/{subject_id}/labels", tags=["labels"])
labels_catalog_router = APIRouter(prefix="/labels", tags=["labels"])


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
    """Authorise label mutations.

    Same tier as assignment management: only the dossier owner (or
    an admin) decides which tags apply. Non-readers get 404 (existence
    leak), readers who aren't the owner get 403 so the UI can grey
    out the editor.
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
            detail="only the dossier owner or an admin can manage labels",
        )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"no saved dossier with id {subject_id!r}",
    )


def _normalise_label(raw: str) -> str:
    """Normalise ``raw`` into the canonical wire form, or 422 on invalid.

    We strip surrounding whitespace, lowercase, and validate against
    :data:`LABEL_PATTERN`. Empty strings, anything containing whitespace
    in the middle, control chars, or > 32 chars are rejected.
    """
    candidate = raw.strip().lower()
    # Use the literal 422 (rather than ``status.HTTP_422_UNPROCESSABLE_ENTITY``)
    # because Starlette has marked the ``HTTP_422_*`` constant deprecated
    # and the project's filterwarnings escalates DeprecationWarning to error.
    if not candidate:
        raise HTTPException(
            status_code=422,
            detail="label must contain at least one non-whitespace character",
        )
    if len(candidate) > LABEL_MAX_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"label exceeds maximum length of {LABEL_MAX_LENGTH} characters",
        )
    if not LABEL_PATTERN.fullmatch(candidate):
        raise HTTPException(
            status_code=422,
            detail=(
                "label may only contain lower-case letters, digits, "
                "'.', '-', and '_'; must start with a letter or digit"
            ),
        )
    return candidate


class _UsernameCache:
    """Resolve ``user_id -> username`` once per request without N+1 lookups."""

    def __init__(self, user_repo: UserRepository) -> None:
        self._user_repo = user_repo
        self._cache: dict[int, str | None] = {}

    def __call__(self, user_id: int | None) -> str | None:
        if user_id is None:
            return None
        if user_id not in self._cache:
            user = self._user_repo.get_by_id(user_id)
            self._cache[user_id] = user.username if user is not None else None
        return self._cache[user_id]


def _label_to_entry(row: LabelRow, resolve: _UsernameCache) -> LabelEntry:
    return LabelEntry(
        label=row.label,
        created_by=resolve(row.created_by),
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# Per-dossier endpoints
# ---------------------------------------------------------------------------


@labels_router.get("", response_model=list[LabelEntry])
def list_labels(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> list[LabelEntry]:
    """List the labels on a dossier (any reader)."""
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    resolve = _UsernameCache(user_repo)
    return [_label_to_entry(row, resolve) for row in access_repo.list_labels(subject_id)]


@labels_router.put(
    "/{label}",
    response_model=list[LabelEntry],
    responses={
        403: {"description": "actor is not the dossier owner or an admin"},
        404: {"description": "subject not found (or actor is not a reader)"},
        422: {"description": "label fails the lower-case alphanumeric pattern"},
    },
)
def add_label(
    subject_id: str,
    label: Annotated[str, Path(min_length=1, max_length=LABEL_MAX_LENGTH)],
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> list[LabelEntry]:
    """Tag a dossier. Idempotent: re-PUTting an existing label is a no-op.

    Returns the full label list afterwards so the UI can re-render the
    chip row from a single response. Owner / admin only — assignees
    and sharers can *see* labels but can't curate them.
    """
    _ensure_owner_or_admin(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    canonical = _normalise_label(label)
    access_repo.add_label(
        subject_id,
        canonical,
        created_by=actor.id,
        created_at=datetime.now(UTC).isoformat(),
    )
    resolve = _UsernameCache(user_repo)
    return [_label_to_entry(row, resolve) for row in access_repo.list_labels(subject_id)]


@labels_router.delete(
    "/{label}",
    response_model=list[LabelEntry],
    responses={
        403: {"description": "actor is not the dossier owner or an admin"},
        404: {"description": "subject not found, or label not present on dossier"},
    },
)
def remove_label(
    subject_id: str,
    label: Annotated[str, Path(min_length=1, max_length=LABEL_MAX_LENGTH)],
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> list[LabelEntry]:
    """Untag a dossier.

    Returns 404 if the label was never on the dossier — DELETE on an
    absent resource is the canonical 404 case (vs. PUT idempotency
    where the absence is the *desired* end-state).
    """
    _ensure_owner_or_admin(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    canonical = _normalise_label(label)
    if not access_repo.remove_label(subject_id, canonical):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"label {canonical!r} is not on subject {subject_id!r}",
        )
    resolve = _UsernameCache(user_repo)
    return [_label_to_entry(row, resolve) for row in access_repo.list_labels(subject_id)]


# ---------------------------------------------------------------------------
# Global catalog
# ---------------------------------------------------------------------------


@labels_catalog_router.get("", response_model=list[LabelCatalogEntry])
def list_label_catalog(
    actor: Annotated[UserRecord, Depends(current_user)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> list[LabelCatalogEntry]:
    """All labels across dossiers visible to the actor, with counts.

    Powers the sidebar's "filter by tag" picker. Counts only include
    dossiers the actor can read; admins see everything (including
    legacy un-owned rows) so they can audit cross-team usage.
    """
    rows = access_repo.list_label_catalog(actor.id, is_admin=actor.role is Role.ADMIN)
    return [LabelCatalogEntry(label=lab, count=count) for lab, count in rows]
