"""Cross-reference endpoint — shared identifiers across dossiers.

Returns, for one source dossier, every other visible dossier that
mentions one of its identifiers, grouped by identifier. The shape is
designed so a frontend can render an "also seen in N other dossiers"
badge per identifier without making N follow-up calls.

Permission model:

- The endpoint is gated by *read access* on the source dossier (404
  otherwise, to avoid leaking subject existence — same posture as
  ``GET /api/v1/subjects/{id}``).
- Each *matched* dossier is filtered by the actor's read access:
  admins see every match, viewers see only matches they own or have
  been explicitly shared. Assignment-grants and other Phase-5+
  read-grants are intentionally *not* honoured here yet — sharing is
  the only mechanism the cross-reference query can rely on without
  pulling more state into the access tables.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from reckora.persistence.repository import SubjectRepository
from reckora_api.access.repository import AccessRepository, CrossReferenceRow
from reckora_api.auth.models import Role, UserRecord
from reckora_api.auth.repository import UserRepository
from reckora_api.deps import (
    current_user,
    get_access_repo,
    get_subject_repo,
    get_user_repo,
)
from reckora_api.investigations.schemas import IdentifierIn
from reckora_api.xref.schemas import (
    CrossReferenceEntry,
    CrossReferenceList,
    CrossReferenceMatch,
)


class _OwnerCache:
    """Memoise per-request ``subject -> owner username`` lookups.

    The cross-reference response can include the same owner across
    multiple matches (or even the same matched subject under several
    shared identifiers), so we cache to avoid hitting the user table
    repeatedly.
    """

    def __init__(self, access_repo: AccessRepository, user_repo: UserRepository) -> None:
        self._access = access_repo
        self._users = user_repo
        self._subject_to_username: dict[str, str | None] = {}

    def owner_username(self, subject_id: str) -> str | None:
        if subject_id in self._subject_to_username:
            return self._subject_to_username[subject_id]
        owner_id = self._access.get_owner(subject_id)
        if owner_id is None:
            self._subject_to_username[subject_id] = None
            return None
        record = self._users.get_by_id(owner_id)
        username = None if record is None else record.username
        self._subject_to_username[subject_id] = username
        return username


router = APIRouter(tags=["cross-references"])


def _ensure_reader(
    subject_id: str,
    user: UserRecord,
    repo: SubjectRepository,
    access_repo: AccessRepository,
) -> None:
    """Mirror ``investigations._load_authorised_dossier`` permission posture.

    Returns silently when the actor can read; otherwise raises 404 so a
    non-owner cannot probe the API for which subject ids exist on the
    system. We don't need the dossier payload here — only the read
    check — so we bypass the engine round-trip and ask the engine
    repository directly whether the subject row exists.
    """
    if user.role is Role.ADMIN:
        if repo.get(subject_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no saved dossier with id {subject_id!r}",
            )
        return
    owner_id = access_repo.get_owner(subject_id)
    if owner_id != user.id and not access_repo.can_read(subject_id, user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no saved dossier with id {subject_id!r}",
        )


def _group_rows(
    rows: list[CrossReferenceRow],
    *,
    owner_cache: _OwnerCache,
) -> list[CrossReferenceEntry]:
    """Collapse access-repo rows into per-identifier groups.

    The repo query already returns rows ordered by
    ``(identifier_type, identifier_value, created_at DESC, id DESC)``,
    so we build groups in a single pass without an extra sort.
    """
    entries: list[CrossReferenceEntry] = []
    current: CrossReferenceEntry | None = None
    for row in rows:
        if (
            current is None
            or current.identifier.kind != row.identifier_type
            or current.identifier.value != row.identifier_value
        ):
            current = CrossReferenceEntry(
                identifier=IdentifierIn(
                    kind=row.identifier_type,
                    value=row.identifier_value,
                ),
                subjects=[],
            )
            entries.append(current)
        current.subjects.append(
            CrossReferenceMatch(
                id=row.matched_subject_id,
                seed=IdentifierIn(
                    kind=row.matched_seed_kind,
                    value=row.matched_seed_value,
                ),
                created_at=datetime.fromisoformat(row.matched_created_at),
                owner_username=owner_cache.owner_username(row.matched_subject_id),
            )
        )
    return entries


@router.get(
    "/subjects/{subject_id}/cross-references",
    response_model=CrossReferenceList,
    responses={
        404: {"description": "subject not found or not visible to actor"},
    },
)
def list_cross_references(
    subject_id: str,
    user: Annotated[UserRecord, Depends(current_user)],
    repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> CrossReferenceList:
    """Return cross-references for ``subject_id`` grouped by shared identifier.

    The response only contains identifiers that have at least one
    cross-referenced dossier visible to the caller — identifiers that
    are unique to this dossier (or whose other matches the caller cannot
    read) are silently dropped. That keeps the payload small and
    matches the "no leakage of invisible subjects" rule from the rest
    of the API.
    """
    _ensure_reader(subject_id, user, repo, access_repo)
    rows = access_repo.list_cross_references(
        subject_id,
        user_id=user.id,
        is_admin=user.role is Role.ADMIN,
    )
    owner_cache = _OwnerCache(access_repo, user_repo)
    return CrossReferenceList(items=_group_rows(rows, owner_cache=owner_cache))
