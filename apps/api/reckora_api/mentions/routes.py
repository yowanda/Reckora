"""HTTP surface for the per-actor mentions feed."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from reckora_api.access.repository import AccessRepository, MentionRow
from reckora_api.auth.models import UserRecord
from reckora_api.auth.repository import UserRepository
from reckora_api.deps import current_user, get_access_repo, get_user_repo
from reckora_api.mentions.schemas import MentionEntry

mentions_router = APIRouter(prefix="/me/mentions", tags=["mentions"])


def _row_to_entry(row: MentionRow, user_repo: UserRepository) -> MentionEntry:
    """Project a :class:`MentionRow` onto the wire shape.

    ``author_username`` is ``None`` if the comment's author has been
    hard-deleted between the mention firing and the feed being read
    — the row stays so the user knows somebody pinged them, but the
    UI can render the card as "deleted user".
    """
    author = user_repo.get_by_id(row.author_user_id)
    return MentionEntry(
        comment_id=row.comment_id,
        subject_id=row.subject_id,
        author_user_id=row.author_user_id,
        author_username=None if author is None else author.username,
        body=row.body,
        comment_created_at=datetime.fromisoformat(row.comment_created_at),
        mention_created_at=datetime.fromisoformat(row.mention_created_at),
    )


@mentions_router.get("", response_model=list[MentionEntry])
def list_my_mentions(
    actor: Annotated[UserRecord, Depends(current_user)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
    limit: int = 50,
) -> list[MentionEntry]:
    """Return the calling actor's mention feed, most-recent first.

    The feed crosses dossiers — a mention on any subject the user
    had access to at the time fires here. We do not re-validate
    visibility on read because the access layer already gates writes:
    if a non-reader can't post a comment, they cannot mint a mention,
    and the row is naturally bounded by who could post when.
    """
    if limit < 0:
        raise HTTPException(
            status_code=422,
            detail="limit must be non-negative",
        )
    rows = access_repo.list_mentions_for_user(actor.id, limit=limit)
    return [_row_to_entry(r, user_repo) for r in rows]
