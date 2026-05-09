"""Collaboration endpoints — per-dossier comments and assignment.

Authorisation policy
--------------------

* **Comments**
    - ``GET`` and ``POST``: any user with read access to the dossier
      (owner, an explicit share, an assignee, or an admin). The whole
      point of comments is to discuss the case among everyone who is
      already allowed to see it; gating writes more tightly than reads
      would force operators to hand-edit the share table just to let an
      assignee leave a note.
    - ``DELETE``: the comment's author, the dossier owner, or an admin.
      Other readers cannot delete someone else's comment because that
      would erase audit trail.

* **Assignees**
    - ``GET``: any user with read access (mirrors the share-list
      endpoint).
    - ``POST`` / ``DELETE``: the dossier owner or an admin only. We
      treat assignment as a management mutation — the same authority
      that can change the share list also controls the assignee list.

Side effects
------------

Adding an assignee implicitly grants them read access through
:meth:`AccessRepository.can_read` (which UNIONs the assignment table
into the visibility check). We deliberately do NOT also write a row
into ``subject_shares`` for the new assignee — the two tables track
distinct intents and an unassign should not silently leave a stale
share row behind.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from reckora.persistence.repository import SubjectRepository
from reckora_api.access.repository import AccessRepository, AssigneeRow, CommentRow
from reckora_api.auth.models import Role, UserRecord
from reckora_api.auth.repository import UserRepository
from reckora_api.collab.schemas import (
    AssigneeCreate,
    AssigneeEntry,
    CommentCreate,
    CommentEntry,
    CommentUpdate,
)
from reckora_api.deps import (
    current_user,
    get_access_repo,
    get_subject_repo,
    get_user_repo,
)
from reckora_api.mentions.parser import extract_mentions

comments_router = APIRouter(prefix="/subjects/{subject_id}/comments", tags=["collab"])
assignees_router = APIRouter(prefix="/subjects/{subject_id}/assignees", tags=["collab"])


# ---------------------------------------------------------------------------
# Authorisation helpers
# ---------------------------------------------------------------------------


def _ensure_subject_exists(subject_id: str, subject_repo: SubjectRepository) -> None:
    """Surface a 404 when the subject does not exist.

    Parity with the sharing endpoints — we don't want an actor to
    distinguish "no such subject" from "no access" by probing the
    collaboration routes.
    """
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
    """Authorise reader-tier collaboration mutations (comment list / create).

    Admins skip the access check so they can triage legacy un-owned
    rows. Everyone else needs an owner / share / assignment row.
    Returns 404 (not 403) when the actor isn't a reader, so the API
    doesn't leak whether a subject id exists to a non-reader.
    """
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
) -> int | None:
    """Authorise owner-tier collaboration mutations (assign / unassign).

    Mirrors the same helper in :mod:`reckora_api.access.routes`: admins
    can manage any subject (including legacy un-owned rows), regular
    users must hold ``subject_owners.owner_user_id``.
    """
    _ensure_subject_exists(subject_id, subject_repo)
    owner_id = access_repo.get_owner(subject_id)
    if actor.role is Role.ADMIN:
        return owner_id
    if owner_id is None or owner_id != actor.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only the dossier owner can manage this resource",
        )
    return owner_id


# ---------------------------------------------------------------------------
# Username resolution
# ---------------------------------------------------------------------------


class _UsernameCache:
    """Cache user lookups inside a single request so a long thread or
    assignee list doesn't re-query the user table for every row.
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


def _comment_to_entry(
    row: CommentRow,
    resolve: _UsernameCache,
    *,
    mentions: list[str] | None = None,
) -> CommentEntry:
    return CommentEntry(
        id=row.id,
        author_user_id=row.author_user_id,
        author_username=resolve(row.author_user_id),
        body=row.body,
        created_at=datetime.fromisoformat(row.created_at),
        updated_at=None if row.updated_at is None else datetime.fromisoformat(row.updated_at),
        mentions=[] if mentions is None else mentions,
        parent_comment_id=row.parent_comment_id,
    )


def _resolve_mentions(
    *,
    body: str,
    subject_id: str,
    comment_id: int,
    created_at: str,
    user_repo: UserRepository,
    access_repo: AccessRepository,
) -> list[str]:
    """Parse ``@username`` tokens out of ``body`` and persist the mentions.

    Unknown usernames and users without read access to the dossier
    are dropped silently — the auth layer already enforces who can
    *post* a comment, but we do not propagate the comment to readers
    who cannot see the dossier (so a passer-by isn't pinged for a
    case they have no business seeing).

    Returns the alphabetically-sorted list of resolved usernames so
    the route can echo them in the wire response.
    """
    candidates = extract_mentions(body)
    resolved: list[str] = []
    for handle in candidates:
        target = user_repo.get_by_username(handle)
        if target is None:
            continue
        owner_id = access_repo.get_owner(subject_id)
        if owner_id != target.id and not access_repo.can_read(subject_id, target.id):
            continue
        access_repo.add_mention(
            comment_id,
            target.id,
            created_at=created_at,
        )
        resolved.append(target.username)
    return sorted(resolved)


def _assignee_to_entry(
    row: AssigneeRow,
    *,
    user_repo: UserRepository,
    resolve: _UsernameCache,
) -> AssigneeEntry | None:
    """Materialise an :class:`AssigneeEntry`, dropping orphan rows.

    Returns ``None`` when the assignee user has been hard-deleted but
    the cascade hasn't fired yet (defensive — the schema's ``ON DELETE
    CASCADE`` should keep this from happening). The caller filters those
    out so the API never surfaces a row with ``username = None`` for
    the assignee proper.
    """
    record = user_repo.get_by_id(row.user_id)
    if record is None:  # pragma: no cover - defensive against schema drift
        return None
    return AssigneeEntry(
        user_id=row.user_id,
        username=record.username,
        assigned_by_user_id=row.assigned_by,
        assigned_by_username=resolve(row.assigned_by),
        assigned_at=datetime.fromisoformat(row.assigned_at),
    )


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


@comments_router.get(
    "",
    response_model=list[CommentEntry],
    responses={404: {"description": "subject not found or not visible to actor"}},
)
def list_comments(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> list[CommentEntry]:
    """List every comment on ``subject_id``, oldest first."""
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    resolve = _UsernameCache(user_repo)
    out: list[CommentEntry] = []
    for r in access_repo.list_comments(subject_id):
        mention_ids = access_repo.list_mentions_for_comment(r.id)
        mention_names: list[str] = []
        for uid in mention_ids:
            name = resolve(uid)
            if name is not None:
                mention_names.append(name)
        # Sort for stable wire output regardless of insertion order.
        out.append(_comment_to_entry(r, resolve, mentions=sorted(mention_names)))
    return out


@comments_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=CommentEntry,
    responses={404: {"description": "subject not found or not visible to actor"}},
)
def create_comment(
    subject_id: str,
    payload: CommentCreate,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> CommentEntry:
    """Append a comment to ``subject_id``'s thread.

    The body is stored verbatim — sanitisation is the renderer's job
    so the raw text stays intact for audit and exfil-on-export.
    """
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    body = payload.body.strip()
    if not body:
        # Use the integer literal because starlette renamed the constant
        # (UNPROCESSABLE_ENTITY → UNPROCESSABLE_CONTENT) and importing the
        # old name now raises a DeprecationWarning, which our pytest
        # config promotes to an error.
        raise HTTPException(
            status_code=422,
            detail="comment body must contain at least one non-whitespace character",
        )
    if payload.parent_comment_id is not None:
        parent = access_repo.get_comment(payload.parent_comment_id)
        if parent is None or parent.subject_id != subject_id:
            # Either the parent does not exist, or it lives on a different
            # subject — either way we 404 to avoid leaking whether the id
            # is valid in some other dossier the actor cannot see.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"no parent comment with id {payload.parent_comment_id} "
                    f"on subject {subject_id!r}"
                ),
            )
        if access_repo.is_reply(payload.parent_comment_id):
            # Threads are flat: a reply cannot have replies. The UI is
            # expected to nudge the user to reply to the *root* of the
            # thread instead of the in-thread message.
            raise HTTPException(
                status_code=422,
                detail="replies are one level deep; reply to the parent comment instead",
            )
    now = datetime.now(UTC).isoformat()
    row = access_repo.add_comment(
        subject_id,
        actor.id,
        body,
        created_at=now,
        parent_comment_id=payload.parent_comment_id,
    )
    resolved_mentions = _resolve_mentions(
        body=body,
        subject_id=subject_id,
        comment_id=row.id,
        created_at=now,
        user_repo=user_repo,
        access_repo=access_repo,
    )
    resolve = _UsernameCache(user_repo)
    return _comment_to_entry(row, resolve, mentions=resolved_mentions)


@comments_router.patch(
    "/{comment_id}",
    response_model=CommentEntry,
    responses={
        403: {"description": "actor is not the comment author"},
        404: {"description": "subject or comment not found"},
    },
)
def update_comment(
    subject_id: str,
    comment_id: int,
    payload: CommentUpdate,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> CommentEntry:
    """Edit a comment's body in place.

    Authorisation is **author-only** — not even the dossier owner or
    an admin can rewrite someone else's words. That's a deliberate
    asymmetry with delete: deletion already preserves audit
    integrity (the comment vanishes; nobody is on the hook for what
    they didn't say), while editing somebody else's comment would
    *attribute new words to them*. Owners and admins who want to
    suppress an objectionable comment can still delete it.

    Other readers (sharers, assignees, outsiders) get the same 404
    we use elsewhere so we don't leak comment existence. The author
    themselves keeps editing access even after losing read access on
    the dossier (e.g. their share was revoked) — being on the hook
    for your own past words trumps the access window.
    """
    _ensure_subject_exists(subject_id, subject_repo)
    comment = access_repo.get_comment(comment_id)
    if comment is None or comment.subject_id != subject_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no comment with id {comment_id} on subject {subject_id!r}",
        )
    if comment.author_user_id != actor.id:
        # Owners / admins / sharers / assignees can all *see* the
        # comment, so a 403 here is the honest answer for readers
        # while still giving outsiders a 404 to mirror the rest of
        # the API surface.
        if actor.role is Role.ADMIN or access_repo.can_read(subject_id, actor.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="only the comment author can edit it",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no comment with id {comment_id} on subject {subject_id!r}",
        )
    body = payload.body.strip()
    if not body:
        raise HTTPException(
            status_code=422,
            detail="comment body must contain at least one non-whitespace character",
        )
    updated = access_repo.update_comment(
        comment_id,
        body,
        updated_at=datetime.now(UTC).isoformat(),
    )
    if updated is None:  # pragma: no cover - guarded by the get_comment above
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no comment with id {comment_id} on subject {subject_id!r}",
        )
    resolve = _UsernameCache(user_repo)
    return _comment_to_entry(updated, resolve)


@comments_router.get(
    "/{comment_id}/replies",
    response_model=list[CommentEntry],
    responses={404: {"description": "subject or parent comment not found"}},
)
def list_replies(
    subject_id: str,
    comment_id: int,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> list[CommentEntry]:
    """List every reply to ``comment_id``, oldest first.

    The parent comment must exist and live on ``subject_id`` —
    cross-subject id smuggling 404s for the same reason ``DELETE``
    does. Visibility is read-tier: any reader of the dossier can see
    the reply thread.
    """
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    parent = access_repo.get_comment(comment_id)
    if parent is None or parent.subject_id != subject_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no comment with id {comment_id} on subject {subject_id!r}",
        )
    resolve = _UsernameCache(user_repo)
    return [_comment_to_entry(r, resolve) for r in access_repo.list_replies(comment_id)]


@comments_router.delete(
    "/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        403: {"description": "actor is not author / owner / admin"},
        404: {"description": "subject or comment not found"},
    },
)
def delete_comment(
    subject_id: str,
    comment_id: int,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> None:
    """Delete a comment.

    The author can always delete their own comment; the dossier owner
    or an admin can delete any comment. Other readers — even if they
    are assignees / sharers — get 403 to preserve audit integrity.
    """
    _ensure_subject_exists(subject_id, subject_repo)
    comment = access_repo.get_comment(comment_id)
    if comment is None or comment.subject_id != subject_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no comment with id {comment_id} on subject {subject_id!r}",
        )
    is_admin = actor.role is Role.ADMIN
    is_author = comment.author_user_id == actor.id
    is_owner = access_repo.get_owner(subject_id) == actor.id
    if not (is_admin or is_author or is_owner):
        # Non-admin / non-author / non-owner readers (assignees, sharers)
        # *can* see the comment but cannot remove it — the audit trail
        # is intentional. We return 403 (rather than 404) here because
        # they already proved they can read by getting this far.
        if access_repo.can_read(subject_id, actor.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="only the comment author, dossier owner, or an admin can delete it",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no comment with id {comment_id} on subject {subject_id!r}",
        )
    access_repo.delete_comment(comment_id)
    return None


# ---------------------------------------------------------------------------
# Assignees
# ---------------------------------------------------------------------------


@assignees_router.get(
    "",
    response_model=list[AssigneeEntry],
    responses={404: {"description": "subject not found or not visible to actor"}},
)
def list_assignees(
    subject_id: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> list[AssigneeEntry]:
    """List every user currently assigned to ``subject_id``."""
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    resolve = _UsernameCache(user_repo)
    out: list[AssigneeEntry] = []
    for row in access_repo.list_assignees(subject_id):
        entry = _assignee_to_entry(row, user_repo=user_repo, resolve=resolve)
        if entry is not None:
            out.append(entry)
    return out


@assignees_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=AssigneeEntry,
    responses={
        403: {"description": "actor is not the owner / not an admin"},
        404: {"description": "subject or target user not found"},
    },
)
def create_assignee(
    subject_id: str,
    payload: AssigneeCreate,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> AssigneeEntry:
    """Assign ``payload.username`` to ``subject_id``.

    Idempotent: re-assigning an already-assigned user echoes the
    existing row so clients that retry on a flaky network get a stable
    response. Self-assigning the owner is allowed — owners may want
    to formally mark themselves as the lead.
    """
    _ensure_owner_or_admin(
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
    ts = datetime.now(UTC).isoformat()
    access_repo.add_assignee(
        subject_id,
        target.id,
        assigned_by=actor.id,
        assigned_at=ts,
    )
    resolve = _UsernameCache(user_repo)
    for row in access_repo.list_assignees(subject_id):
        if row.user_id == target.id:
            entry = _assignee_to_entry(row, user_repo=user_repo, resolve=resolve)
            if entry is not None:
                return entry
            break
    # Should never reach here because we just inserted the row, but fall back
    # to the timestamp we used so the contract still holds.
    return AssigneeEntry(  # pragma: no cover - belt-and-braces
        user_id=target.id,
        username=target.username,
        assigned_by_user_id=actor.id,
        assigned_by_username=actor.username,
        assigned_at=datetime.fromisoformat(ts),
    )


@assignees_router.delete(
    "/{username}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        403: {"description": "actor is not the owner / not an admin"},
        404: {"description": "subject, user, or assignment not found"},
    },
)
def revoke_assignee(
    subject_id: str,
    username: str,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> None:
    """Unassign ``username`` from ``subject_id``."""
    _ensure_owner_or_admin(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    target = user_repo.get_by_username(username)
    if target is None or not access_repo.remove_assignee(subject_id, target.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no assignment for user {username!r} on subject {subject_id!r}",
        )
    return None


# Re-export for the app factory; keeping a tuple makes the import in main.py
# read like the existing `auth_router, users_router` pair.
__all__: tuple[str, ...] = (
    "assignees_router",
    "comments_router",
)
