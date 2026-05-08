"""Schemas + allow-list for comment reactions."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

# Stable string keys (à la GitHub) so the wire protocol does not depend
# on which side of an emoji rendering bug a client lives on. Adding to
# this set is a forward-compatible change; removing from it is not, so
# only extend.
ALLOWED_REACTION_KEYS: frozenset[str] = frozenset(
    {
        "+1",
        "-1",
        "heart",
        "eyes",
        "fire",
        "tada",
        "rocket",
        "thinking",
    }
)


class ReactionGroup(BaseModel):
    """One emoji bucket within a comment's reaction summary.

    ``users`` is intentionally surfaced rather than just ``count`` so
    the UI can render hover tooltips ("alice, bob and 2 others") and
    so audit exports can attribute reactions back to specific users
    without a second round-trip per group.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    count: int
    users: list[str]
    me_reacted: bool
