"""Comment-reaction endpoints.

Authorisation policy
--------------------

* **Read** (``GET ../reactions``): any reader of the parent dossier.
  We mirror the comments-list policy so a reader who can already see
  the comment can also see who reacted.
* **Write** (``PUT ../reactions/{key}``): same gate — any reader of
  the dossier can react to any comment they can see.
* **Delete** (``DELETE ../reactions/{key}``): the actor can remove
  *their own* reaction. Removing somebody else's reaction is never
  permitted (no admin override) — reactions are personal expressions
  and rewriting them would be tantamount to putting words in the
  reactor's mouth.

A reactor who later loses read access keeps their existing reactions
in the table; we don't auto-revoke. The summary endpoint will simply
not surface to them anymore (they can't see the comment), which is
the natural enforcement mechanism.

Dependencies on other modules
-----------------------------

We re-implement ``_ensure_reader`` and ``_UsernameCache`` here rather
than importing from :mod:`reckora_api.collab.routes` to keep the
reactions module standalone — exactly the same call shape, no
behavioural drift, and no circular import risk if collab ever wants
to depend on reactions for badge counts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from reckora.persistence.repository import SubjectRepository
from reckora_api.access.repository import AccessRepository, ReactionRow
from reckora_api.auth.models import Role, UserRecord
from reckora_api.auth.repository import UserRepository
from reckora_api.deps import (
    current_user,
    get_access_repo,
    get_subject_repo,
    get_user_repo,
)
from reckora_api.reactions.schemas import ALLOWED_REACTION_KEYS, ReactionGroup

router = APIRouter(
    prefix="/subjects/{subject_id}/comments/{comment_id}/reactions",
    tags=["collab"],
)


def _ensure_reader(
    *,
    subject_id: str,
    actor: UserRecord,
    subject_repo: SubjectRepository,
    access_repo: AccessRepository,
) -> None:
    if subject_repo.get(subject_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no saved dossier with id {subject_id!r}",
        )
    if actor.role is Role.ADMIN:
        return
    if not access_repo.can_read(subject_id, actor.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no saved dossier with id {subject_id!r}",
        )


def _resolve_comment(
    *,
    subject_id: str,
    comment_id: int,
    access_repo: AccessRepository,
) -> None:
    """Reject a stale comment id or one that points to a different subject.

    The reaction routes are scoped under ``/subjects/{sid}/comments/{cid}``
    but ``comment_id`` is globally unique, so a malicious caller could
    smuggle the id from one subject into another's URL. We 404 in both
    cases to mirror the rest of the API surface and avoid leaking
    cross-subject comment existence.
    """
    comment = access_repo.get_comment(comment_id)
    if comment is None or comment.subject_id != subject_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no comment with id {comment_id} on subject {subject_id!r}",
        )


def _ensure_known_key(reaction_key: str) -> None:
    if reaction_key not in ALLOWED_REACTION_KEYS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown reaction key {reaction_key!r}",
        )


def _summarise(
    rows: list[ReactionRow],
    *,
    user_repo: UserRepository,
    me_id: int,
) -> list[ReactionGroup]:
    """Pivot the row-level reactions into the per-emoji summary.

    The repository hands us rows already sorted by
    ``(reaction_key, created_at, user_id)``, so a single pass is
    enough — we group by ``reaction_key`` while accumulating users
    and noting whether the calling user appears.
    """
    groups: list[ReactionGroup] = []
    cache: dict[int, str | None] = {}

    def resolve(uid: int) -> str | None:
        if uid not in cache:
            record = user_repo.get_by_id(uid)
            cache[uid] = None if record is None else record.username
        return cache[uid]

    current_key: str | None = None
    current_users: list[str] = []
    current_me: bool = False

    for row in rows:
        if row.reaction_key != current_key:
            if current_key is not None:
                groups.append(
                    ReactionGroup(
                        key=current_key,
                        count=len(current_users),
                        users=current_users,
                        me_reacted=current_me,
                    )
                )
            current_key = row.reaction_key
            current_users = []
            current_me = False
        username = resolve(row.user_id)
        if username is not None:  # pragma: no branch - cascade keeps these aligned
            current_users.append(username)
        if row.user_id == me_id:
            current_me = True

    if current_key is not None:
        groups.append(
            ReactionGroup(
                key=current_key,
                count=len(current_users),
                users=current_users,
                me_reacted=current_me,
            )
        )
    return groups


@router.get(
    "",
    response_model=list[ReactionGroup],
    responses={404: {"description": "subject or comment not found / not visible"}},
)
def list_reactions(
    subject_id: str,
    comment_id: int,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> list[ReactionGroup]:
    """Return the reaction summary for ``comment_id`` (one entry per
    emoji that has at least one reactor)."""
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    _resolve_comment(subject_id=subject_id, comment_id=comment_id, access_repo=access_repo)
    rows = access_repo.list_reactions(comment_id)
    return _summarise(rows, user_repo=user_repo, me_id=actor.id)


@router.put(
    "/{reaction_key}",
    response_model=list[ReactionGroup],
    responses={
        404: {"description": "subject or comment not found / not visible"},
        422: {"description": "reaction_key not in the allow-list"},
    },
)
def add_reaction(
    subject_id: str,
    comment_id: int,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
    reaction_key: Annotated[str, Path(min_length=1, max_length=32)],
) -> list[ReactionGroup]:
    """Add ``reaction_key`` to ``comment_id`` for the calling user.

    Idempotent — re-adding an existing reaction returns the same
    summary without bumping any counters. The endpoint always
    responds with the *full* updated summary (not just the affected
    bucket) so a client doing optimistic UI doesn't have to merge
    payloads.
    """
    _ensure_known_key(reaction_key)
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    _resolve_comment(subject_id=subject_id, comment_id=comment_id, access_repo=access_repo)
    access_repo.add_reaction(
        comment_id,
        actor.id,
        reaction_key,
        created_at=datetime.now(UTC).isoformat(),
    )
    rows = access_repo.list_reactions(comment_id)
    return _summarise(rows, user_repo=user_repo, me_id=actor.id)


@router.delete(
    "/{reaction_key}",
    response_model=list[ReactionGroup],
    responses={
        404: {"description": "subject / comment not found, or actor never reacted"},
        422: {"description": "reaction_key not in the allow-list"},
    },
)
def remove_reaction(
    subject_id: str,
    comment_id: int,
    actor: Annotated[UserRecord, Depends(current_user)],
    subject_repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
    reaction_key: Annotated[str, Path(min_length=1, max_length=32)],
) -> list[ReactionGroup]:
    """Remove the calling user's ``reaction_key`` reaction.

    Returns 404 if the actor never had that reaction so a stale
    optimistic UI can't pretend a no-op succeeded. Removing
    somebody else's reaction is not supported — every actor can
    only roll back *their own* expression.
    """
    _ensure_known_key(reaction_key)
    _ensure_reader(
        subject_id=subject_id,
        actor=actor,
        subject_repo=subject_repo,
        access_repo=access_repo,
    )
    _resolve_comment(subject_id=subject_id, comment_id=comment_id, access_repo=access_repo)
    if not access_repo.remove_reaction(comment_id, actor.id, reaction_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(f"no {reaction_key!r} reaction by the calling user on comment {comment_id}"),
        )
    rows = access_repo.list_reactions(comment_id)
    return _summarise(rows, user_repo=user_repo, me_id=actor.id)


__all__: tuple[str, ...] = ("router",)
