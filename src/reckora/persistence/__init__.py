"""Persistence layer for Reckora dossiers.

Phase 2 introduces durable storage behind a thin repository seam so that
investigations are inspectable later without re-collecting evidence. Callers
should depend on :class:`SubjectRepository`; concrete implementations live in
sibling modules:

* :class:`SQLiteSubjectRepository` — default, file-backed (or in-memory),
  zero external dependencies.
* :class:`Neo4jSubjectRepository` — opt-in graph backend that shares
  ``Identifier`` nodes across subjects so a follow-up Cypher query can list
  every dossier that ever touched a given identifier. Pull the optional
  ``neo4j`` driver with ``uv sync --extra neo4j`` (or
  ``pip install 'reckora[neo4j]'``) before importing it.

The Neo4j adapter is **lazily imported** so that an environment that has not
installed the optional driver can still ``import reckora.persistence``
without crashing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .repository import SavedDossier, SavedDossierSummary, SubjectRepository
from .sqlite import SQLiteSubjectRepository

if TYPE_CHECKING:
    from .neo4j_repo import Neo4jSubjectRepository

__all__ = [
    "Neo4jSubjectRepository",
    "SQLiteSubjectRepository",
    "SavedDossier",
    "SavedDossierSummary",
    "SubjectRepository",
]


def __getattr__(name: str) -> Any:
    """Lazily resolve :class:`Neo4jSubjectRepository`.

    Importing the symbol triggers an import of the optional ``neo4j``
    driver, so we defer it until first use to keep the default install path
    free of unnecessary work.
    """
    if name == "Neo4jSubjectRepository":
        from .neo4j_repo import Neo4jSubjectRepository

        return Neo4jSubjectRepository
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
