"""HTTP surface for per-actor TODO checklists on dossiers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from reckora.persistence.repository import SubjectRepository
from reckora_api.access.repository import AccessRepository, TodoRow
from reckora_api.auth.models import UserRecord
from reckora_api.deps import current_user, get_access_repo, get_subject_repo
from reckora_api.todos.schemas import TodoCreate, TodoEntry, TodoUpdate

todos_router = APIRouter(
    prefix="/subjects/{subject_id}/todos",
    tags=["todos"],
)


def _ensure_reader(
    *,
    subject_id: str,
    actor: UserRecord,
    subject_repo: SubjectRepository,
    access_repo: AccessRepository,
) -> None:
    """Reject the caller with 404 if they cannot read ``subject_id``.

    Unknown-subject and no-access collapse to the same 404 so a
    non-reader cannot probe for the existence of a dossier.
    """
    if subject_repo.get(subject_id) is None:
        raise HTTPException(status_code=404, detail="subject not found")
    if not access_repo.can_read(subject_id, actor.id):
        raise HTTPException(status_code=404, detail="subject not found")


def _row_to_entry(row: TodoRow) -> TodoEntry:
    return TodoEntry(
        id=row.id,
        subject_id=row.subject_id,
        user_id=row.user_id,
        body=row.body,
        done=row.done,
        created_at=datetime.fromisoformat(row.created_at),
        updated_at=datetime.fromisoformat(row.updated_at),
    )


def _get_owned_or_404(
    *,
    todo_id: int,
    subject_id: str,
    actor: UserRecord,
    access_repo: AccessRepository,
) -> TodoRow:
    """Resolve ``todo_id`` and 404 if it isn't the actor's row.

    We collapse "not yours", "wrong subject", and "never existed"
    into a single 404 so a curious actor cannot probe for the
    existence of someone else's checklist items by guessing ids,
    even if they have read access to the underlying dossier.
    """
    row = access_repo.get_todo(todo_id)
    if row is None or row.subject_id != subject_id or row.user_id != actor.id:
        raise HTTPException(status_code=404, detail="todo not found")
    return row


@todos_router.get("/me", response_model=list[TodoEntry])
def list_my_todos(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> list[TodoEntry]:
    """Return the actor's TODOs on ``subject_id``, oldest first."""
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    rows = access_repo.list_todos(subject_id, actor.id)
    return [_row_to_entry(r) for r in rows]


@todos_router.post(
    "/me",
    response_model=TodoEntry,
    status_code=201,
    responses={
        404: {"description": "subject not found or not visible to actor"},
        422: {"description": "body must be 1..512 chars"},
    },
)
def create_my_todo(
    subject_id: str,
    payload: TodoCreate,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> TodoEntry:
    """Append a new TODO to the actor's checklist on ``subject_id``."""
    body = payload.body.strip()
    if not body:
        raise HTTPException(
            status_code=422,
            detail="todo body must contain at least one non-whitespace character",
        )
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    row = access_repo.create_todo(
        subject_id,
        actor.id,
        body,
        now=datetime.now(UTC).isoformat(),
    )
    return _row_to_entry(row)


@todos_router.patch(
    "/me/{todo_id}",
    response_model=TodoEntry,
    responses={
        404: {"description": "subject or todo not found / not yours"},
        422: {"description": "patch must update at least one field"},
    },
)
def update_my_todo(
    subject_id: str,
    todo_id: int,
    payload: TodoUpdate,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> TodoEntry:
    """Toggle ``done`` and/or rewrite ``body`` on one of the actor's todos."""
    if payload.body is None and payload.done is None:
        raise HTTPException(
            status_code=422,
            detail="patch must update at least one of body or done",
        )
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    _get_owned_or_404(
        todo_id=todo_id,
        subject_id=subject_id,
        actor=actor,
        access_repo=access_repo,
    )
    body = payload.body.strip() if payload.body is not None else None
    if body is not None and not body:
        # All-whitespace edit: 422, the same way create rejects an
        # empty body. We do not silently keep the previous body,
        # because that would be surprising.
        raise HTTPException(
            status_code=422,
            detail="todo body must contain at least one non-whitespace character",
        )
    updated = access_repo.update_todo(
        todo_id,
        body=body,
        done=payload.done,
        now=datetime.now(UTC).isoformat(),
    )
    if updated is None:
        # Theoretically unreachable: ownership was verified above.
        # Surface it cleanly rather than raising AttributeError
        # if a concurrent delete races us.
        raise HTTPException(status_code=404, detail="todo not found")
    return _row_to_entry(updated)


@todos_router.delete("/me/{todo_id}", status_code=204)
def delete_my_todo(
    subject_id: str,
    todo_id: int,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> Response:
    """Delete one of the actor's todos. 404 if not theirs.

    Unlike notes/visits, we *do* re-check ownership here. A silent
    204 on "not yours / not found" is the right shape for an
    idempotent delete only when the row would have been
    discoverable to begin with. We don't want a non-owner to be
    able to bulk-DELETE someone else's checklist by guessing ids.
    "Not yours" therefore 404s the same way a wrong-subject id
    would.
    """
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    _get_owned_or_404(
        todo_id=todo_id,
        subject_id=subject_id,
        actor=actor,
        access_repo=access_repo,
    )
    access_repo.delete_todo(todo_id)
    return Response(status_code=204)
