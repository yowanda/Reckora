"""Phase 5 step 8 — per-dossier watchers (subscribe / follow).

Each watcher is one row in :class:`subject_watchers` keyed by
``(subject_id, user_id)``. Subscribing is a self-service opt-in for
any reader of the dossier; the route layer never lets one user
subscribe another. Watching does not by itself grant read access —
the cascade fires the other direction (revoking a share / assignment
wipes the watch row).

The catalog endpoint ``GET /api/v1/me/watching`` returns the
watcher's most-recently-followed dossiers as
:class:`SavedDossierSummary` rows so the UI can render the watch-list
in the same shape as the recent / shared lists.
"""

from __future__ import annotations

from reckora_api.watchers.routes import (
    me_watching_router,
    watchers_router,
)
from reckora_api.watchers.schemas import (
    WatcherEntry,
    WatchStatus,
)

__all__ = [
    "WatchStatus",
    "WatcherEntry",
    "me_watching_router",
    "watchers_router",
]
