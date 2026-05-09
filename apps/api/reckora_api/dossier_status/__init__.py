"""Per-dossier status state machine.

Each dossier carries an ``open`` / ``on_hold`` / ``closed`` status that
the case manager flips to communicate triage state to collaborators.
The status row is implicit-default ``open`` (no row in
``subject_status`` means open); a row is materialised the first time
somebody flips status away from the default *or* explicitly back to
open, so the audit trail (``updated_at`` / ``updated_by``) survives
ping-pong transitions.

Authorisation policy
--------------------

* Read:  any reader of the dossier (owner / share / assignment / admin).
* Write: dossier owner or admin only — same tier as label / assignment
  management. Triage state is a management decision; collaborators
  signal opinion through comments / reactions.
"""

from reckora_api.dossier_status.routes import (
    status_catalog_router,
    status_router,
)
from reckora_api.dossier_status.schemas import (
    ALLOWED_STATUSES,
    DEFAULT_STATUS,
    StatusEntry,
    StatusUpdate,
)

__all__ = [
    "ALLOWED_STATUSES",
    "DEFAULT_STATUS",
    "StatusEntry",
    "StatusUpdate",
    "status_catalog_router",
    "status_router",
]
