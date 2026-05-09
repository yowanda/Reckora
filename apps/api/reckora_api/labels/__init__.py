"""Per-dossier labels (free-form tags).

Powers the "filter by tag" UI surface. Labels are simple lower-case
strings stored in :class:`reckora_api.access.repository.AccessRepository`'s
``subject_labels`` table; both per-dossier listing and a global
catalog (used by the dossier index sidebar) live here.

Authorisation policy
--------------------

* Read:  any reader of the dossier (owner / share / assignment / admin).
* Write: dossier owner or admin only — same tier as assignment
  management. We deliberately do NOT let assignees re-tag a dossier:
  labels are organisational metadata that the *case manager* curates,
  not opinion left by collaborators (those go in comments / reactions).
"""

from reckora_api.labels.routes import labels_catalog_router, labels_router
from reckora_api.labels.schemas import LabelCatalogEntry, LabelEntry

__all__ = [
    "LabelCatalogEntry",
    "LabelEntry",
    "labels_catalog_router",
    "labels_router",
]
