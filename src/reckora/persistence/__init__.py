"""Persistence layer for Reckora dossiers.

Phase 2 introduces durable storage behind a thin repository seam so that
investigations are inspectable later without re-collecting evidence. Callers
should depend on :class:`SubjectRepository`; concrete implementations (the
default SQLite one, future Neo4j adapter, etc.) live in sibling modules.
"""

from __future__ import annotations

from .repository import SavedDossier, SavedDossierSummary, SubjectRepository
from .sqlite import SQLiteSubjectRepository

__all__ = [
    "SQLiteSubjectRepository",
    "SavedDossier",
    "SavedDossierSummary",
    "SubjectRepository",
]
