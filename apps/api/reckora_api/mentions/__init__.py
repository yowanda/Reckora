"""Phase 5 step 10 — @username mentions on comments.

The mentions feature wires three things together:

* A regex parser (:mod:`reckora_api.mentions.parser`) that extracts
  candidate usernames from a free-form comment body. Candidates are
  looked up against the user table; unknown usernames are dropped
  silently so a typo doesn't produce a 422.
* A side table ``subject_comment_mentions`` keyed by
  ``(comment_id, mentioned_user_id)``. The route layer materialises
  rows when a comment is created (and on edit, when that surface
  exists) so the mentions feed survives independently of the
  rendered body.
* A per-actor feed (``GET /api/v1/me/mentions``) that returns the
  current user's recent mentions across every dossier they had read
  access to at the time of mention.
"""

from __future__ import annotations

from reckora_api.mentions.parser import extract_mentions
from reckora_api.mentions.routes import mentions_router
from reckora_api.mentions.schemas import MentionEntry

__all__ = [
    "MentionEntry",
    "extract_mentions",
    "mentions_router",
]
