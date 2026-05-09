"""Comment reactions (Phase 5 step 5).

A reaction is a single ``(comment_id, user_id, reaction_key)`` triple
in the ``comment_reactions`` table. The route layer projects these
into a per-emoji summary — count, reactor usernames, ``me_reacted``
— so the UI can render a GitHub-style reaction bar without an N+1
fetch per emoji.

Allowed keys live in :data:`ALLOWED_REACTION_KEYS`. We intentionally
use stable string keys (``"+1"``, ``"heart"``) instead of raw
unicode emojis so the wire protocol stays grep-friendly across
platforms with different emoji rendering, and so we can add
typographic variants later (skin-tone, gender) without breaking
older clients.
"""

from reckora_api.reactions.schemas import ALLOWED_REACTION_KEYS

__all__: tuple[str, ...] = ("ALLOWED_REACTION_KEYS",)
