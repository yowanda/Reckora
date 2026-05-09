"""Phase 5 step 11 — per-actor pinned dossiers (``favourites``).

A pin is a personal bookmark: marking a dossier as pinned only
affects the *calling* actor's favourites list. Pins do not change
share/assignment state and do not propagate to other readers.

Surface:

* ``POST /api/v1/me/pins/{subject_id}`` — pin (idempotent: re-pinning
  is a no-op, the pin keeps its original timestamp).
* ``DELETE /api/v1/me/pins/{subject_id}`` — unpin (idempotent on
  rows that are already absent).
* ``GET /api/v1/me/pins`` — list pinned dossiers, most-recently-
  pinned first. Dossiers the actor has lost access to are silently
  filtered out (the pin row is preserved so the favourite reappears
  if access is later restored).
"""

from __future__ import annotations

from reckora_api.pins.routes import pins_router

__all__ = ["pins_router"]
