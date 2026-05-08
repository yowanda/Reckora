"""Repository seam for persisted dossiers.

The orchestrator emits ephemeral ``Subject`` + ``Trace`` + ``Edge`` tuples.
Phase 2 starts persisting those so investigations remain inspectable after
the process exits, without forcing every consumer to know how the bytes are
stored on disk.

The contract is intentionally narrow: save the result of one investigation,
fetch it back by id, list the most recent ones, and delete by id. Anything
richer (full-text search, graph queries, multi-tenant ACLs) belongs in a
higher layer that composes this seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from ..models.entity import Edge, Identifier, Subject, Trace


@dataclass(frozen=True)
class SavedDossier:
    """A previously-persisted investigation, fully rehydrated.

    The ``subject`` carries the canonical identifier list; ``traces`` and
    ``edges`` are returned alongside it because consumers (the report layer,
    the AI reasoning layer) treat them as first-class arguments rather than
    walking the subject.
    """

    id: str
    subject: Subject
    traces: list[Trace]
    edges: list[Edge]
    created_at: datetime
    summary: str | None = None
    hypotheses: str | None = None


@dataclass(frozen=True)
class SavedDossierSummary:
    """Lightweight metadata row returned by ``list_recent``.

    Cheap to materialise (no trace / edge JSON deserialisation) so the CLI
    ``list`` command stays fast even on large databases.
    """

    id: str
    seed_identifier: Identifier
    created_at: datetime
    identifier_count: int
    trace_count: int
    edge_count: int
    has_summary: bool
    has_hypotheses: bool


@runtime_checkable
class SubjectRepository(Protocol):
    """Persistence seam for Reckora dossiers."""

    def save(
        self,
        *,
        subject: Subject,
        traces: list[Trace],
        edges: list[Edge],
        summary: str | None = None,
        hypotheses: str | None = None,
        created_at: datetime | None = None,
    ) -> SavedDossierSummary:
        """Persist one investigation result and return its summary row.

        Implementations MUST treat ``subject.id`` as the primary key. Calling
        ``save`` twice with the same id MUST replace the previous record so
        re-runs are idempotent.
        """

    def get(self, subject_id: str) -> SavedDossier | None:
        """Return the rehydrated dossier or ``None`` if it does not exist."""

    def list_recent(self, limit: int = 20) -> list[SavedDossierSummary]:
        """Return the ``limit`` most recently saved dossiers, newest first."""

    def delete(self, subject_id: str) -> bool:
        """Delete a dossier. Returns ``True`` if a row was actually removed."""
