"""Pydantic schemas for per-dossier status."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

# Closed allow-list. We deliberately use stable string keys (rather
# than an integer enum) so the wire protocol survives renaming /
# reordering at the source level. Adding a new bucket later is
# backwards-compatible; removing one is not.
ALLOWED_STATUSES: frozenset[str] = frozenset({"open", "on_hold", "closed"})

# What a brand-new dossier reports before anybody touches its status.
DEFAULT_STATUS = "open"


class StatusEntry(BaseModel):
    """Current status of a dossier (implicit-default-aware)."""

    model_config = ConfigDict(extra="forbid")

    status: str
    updated_by: str | None
    updated_at: str | None


class StatusUpdate(BaseModel):
    """Body for ``PUT /api/v1/subjects/{subject_id}/status``."""

    model_config = ConfigDict(extra="forbid")

    status: str
