"""Phase 5 step 12 — per-actor private notes on dossiers.

A note is private to the calling actor: their own scratch-pad
markdown attached to a dossier, completely invisible to every
other reader (including the owner). Notes do not show up in
comments, threads, mentions, the activity feed, or any cross-
actor surface.

Surface:

* ``GET /api/v1/subjects/{id}/notes/me`` — read my note (404 if
  none yet, 403/404 collapsed if I cannot read the dossier).
* ``PUT /api/v1/subjects/{id}/notes/me`` — upsert. First write
  sets ``created_at``; subsequent writes only advance
  ``updated_at``.
* ``DELETE /api/v1/subjects/{id}/notes/me`` — delete the note.

The body is plain text / markdown bounded at 16 KiB so a runaway
client doesn't bloat the database.
"""

from __future__ import annotations

from reckora_api.notes.routes import notes_router
from reckora_api.notes.schemas import NoteEntry, NoteUpsert

__all__ = ["NoteEntry", "NoteUpsert", "notes_router"]
