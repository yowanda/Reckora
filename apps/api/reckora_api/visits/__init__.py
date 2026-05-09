"""Phase 5 step 13 — per-actor dossier visit stamps + unread comment counts.

A *visit stamp* is the last-seen ISO timestamp recorded against a
``(subject, user)`` pair. The FE bumps it whenever the actor opens
the dossier; everything else \u2014 the unread badge, the
"new since you were here" cutoff \u2014 is derived from that single
piece of state.

Surface:

* ``POST /api/v1/subjects/{id}/visits/me`` &rarr; advance the
  caller's stamp to *now*. Returns the new stamp inline so the FE
  doesn't need a follow-up GET.
* ``GET  /api/v1/subjects/{id}/visits/me`` &rarr; read the stamp
  (404 if never visited).
* ``GET  /api/v1/subjects/{id}/unread`` &rarr; the per-actor unread
  count, plus the visit stamp the count is relative to.
"""

from __future__ import annotations

from reckora_api.visits.routes import visits_router
from reckora_api.visits.schemas import UnreadStatus, VisitEntry

__all__ = ["UnreadStatus", "VisitEntry", "visits_router"]
