"""HTTP surface for per-actor private notes on dossiers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from reckora.persistence.repository import SubjectRepository
from reckora_api.access.repository import AccessRepository, NoteRow
from reckora_api.auth.models import UserRecord
from reckora_api.deps import current_user, get_access_repo, get_subject_repo
from reckora_api.notes.schemas import NoteEntry, NoteUpsert

notes_router = APIRouter(
    prefix="/subjects/{subject_id}/notes",
    tags=["notes"],
)


def _ensure_reader(
    *,
    subject_id: str,
    actor: UserRecord,
    subject_repo: SubjectRepository,
    access_repo: AccessRepository,
) -> None:
    """Reject the caller with 404 if they cannot read the dossier.

    We collapse the unknown-subject and no-access cases into the same
    404 so a non-reader cannot probe for the existence of a dossier
    they're not allowed to see.
    """
    if subject_repo.get(subject_id) is None:
        raise HTTPException(status_code=404, detail="subject not found")
    if not access_repo.can_read(subject_id, actor.id):
        raise HTTPException(status_code=404, detail="subject not found")


def _row_to_entry(row: NoteRow) -> NoteEntry:
    return NoteEntry(
        subject_id=row.subject_id,
        user_id=row.user_id,
        body=row.body,
        created_at=datetime.fromisoformat(row.created_at),
        updated_at=datetime.fromisoformat(row.updated_at),
    )


@notes_router.get(
    "/me",
    response_model=NoteEntry,
    responses={
        404: {"description": "subject not found, not visible, or no note yet"},
    },
)
def get_my_note(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> NoteEntry:
    """Return the calling actor's private note on ``subject_id``."""
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    note = access_repo.get_note(subject_id, actor.id)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    return _row_to_entry(note)


@notes_router.put(
    "/me",
    response_model=NoteEntry,
    responses={
        404: {"description": "subject not found or not visible to actor"},
        422: {"description": "body must be 1..16 KiB"},
    },
)
def upsert_my_note(
    subject_id: str,
    payload: NoteUpsert,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> NoteEntry:
    """Create or replace the calling actor's private note.

    The body is also stripped of leading/trailing whitespace before
    validation so a payload of ``"   "`` 422s exactly the way an
    empty string does \u2014 a whitespace-only note has no value
    over having no note at all.
    """
    body = payload.body.strip()
    if not body:
        raise HTTPException(
            status_code=422,
            detail="note body must contain at least one non-whitespace character",
        )
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    row = access_repo.upsert_note(
        subject_id,
        actor.id,
        body,
        now=datetime.now(UTC).isoformat(),
    )
    return _row_to_entry(row)


@notes_router.delete("/me", status_code=204)
def delete_my_note(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> Response:
    """Delete the calling actor's private note. Idempotent.

    We still gate on read access here \u2014 a non-reader has no
    business interacting with the dossier surface at all, even if
    the side-effect is a no-op for them.
    """
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    access_repo.delete_note(subject_id, actor.id)
    return Response(status_code=204)
