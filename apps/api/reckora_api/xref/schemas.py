"""Pydantic models for the cross-reference endpoint."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from reckora_api.investigations.schemas import IdentifierIn


class CrossReferenceMatch(BaseModel):
    """One dossier that shares an identifier with the source subject."""

    model_config = ConfigDict(extra="forbid")

    id: str
    seed: IdentifierIn
    created_at: datetime
    owner_username: str | None = None


class CrossReferenceEntry(BaseModel):
    """One identifier from the source dossier and the dossiers that share it.

    ``subjects`` is sorted newest-first. Identifiers with no cross
    references (i.e. unique to the source dossier) are not emitted —
    callers only ever see entries that have at least one match.
    """

    model_config = ConfigDict(extra="forbid")

    identifier: IdentifierIn
    subjects: list[CrossReferenceMatch]


class CrossReferenceList(BaseModel):
    """Body of ``GET /api/v1/subjects/{id}/cross-references``."""

    model_config = ConfigDict(extra="forbid")

    items: list[CrossReferenceEntry]
