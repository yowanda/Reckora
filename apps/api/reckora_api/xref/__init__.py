"""Cross-reference endpoints (Phase 5 — shared evidence library).

For a given dossier, surface every *other* visible dossier that mentions
one of its identifiers. The lookup is access-filtered against
:class:`reckora_api.access.repository.AccessRepository` so a viewer
never sees a subject they couldn't otherwise read.
"""
