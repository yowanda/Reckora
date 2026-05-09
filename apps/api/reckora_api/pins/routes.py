"""HTTP surface for ``GET/POST/DELETE /api/v1/me/pins``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from reckora.persistence.repository import SavedDossierSummary, SubjectRepository
from reckora_api.access.repository import AccessRepository
from reckora_api.auth.models import UserRecord
from reckora_api.deps import current_user, get_access_repo, get_subject_repo

pins_router = APIRouter(prefix="/me/pins", tags=["pins"])


def _ensure_subject_exists(subject_id: str, subject_repo: SubjectRepository) -> None:
    if subject_repo.get(subject_id) is None:
        raise HTTPException(status_code=404, detail="subject not found")


@pins_router.get("", response_model=list[SavedDossierSummary])
def list_my_pins(
    actor: Annotated[UserRecord, Depends(current_user)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    limit: int = 50,
) -> list[SavedDossierSummary]:
    """Return the calling actor's pinned dossiers, most-recent first.

    Dossiers the actor has lost access to are silently filtered out
    \u2014 their pin row is left in place so the favourite resurrects
    if access is later restored.
    """
    if limit < 0:
        raise HTTPException(status_code=422, detail="limit must be non-negative")
    return access_repo.list_pinned_summaries(actor.id, limit=limit)


@pins_router.post(
    "/{subject_id}",
    status_code=204,
    responses={
        404: {"description": "subject not found or not visible to actor"},
    },
)
def pin_subject(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> Response:
    """Pin ``subject_id`` for the calling actor.

    The actor must currently have read access to the dossier
    (owner, share, or assignment). We collapse the existence and
    permission checks into a single 404 \u2014 distinguishing them
    would leak whether some hidden dossier with this id exists.
    Idempotent: a no-op when the pin already exists.
    """
    _ensure_subject_exists(subject_id, subject_repo)
    if not access_repo.can_read(subject_id, actor.id):
        # Same wire shape as "subject not found" so a non-reader
        # cannot probe for existence by pinning.
        raise HTTPException(status_code=404, detail="subject not found")
    access_repo.add_pin(
        subject_id,
        actor.id,
        pinned_at=datetime.now(UTC).isoformat(),
    )
    return Response(status_code=204)


@pins_router.delete("/{subject_id}", status_code=204)
def unpin_subject(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> Response:
    """Drop the actor's pin on ``subject_id``.

    Idempotent: returns 204 even if the pin never existed. We
    deliberately do *not* check current visibility here \u2014 a user
    must always be able to clear an orphaned pin (e.g. after their
    share was revoked) without first regaining access. There is no
    information-leak surface because removing a non-existent pin
    has no observable side-effect.
    """
    access_repo.remove_pin(subject_id, actor.id)
    return Response(status_code=204)
