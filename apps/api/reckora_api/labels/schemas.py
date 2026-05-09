"""Pydantic schemas for per-dossier labels."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

# Constraints for an acceptable label string. Lower-case alphanumerics,
# dashes, dots, underscores; 1-32 chars. The route layer normalises
# the input to lower-case before applying this regex so callers can
# send "OSINT" without it failing validation.
LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
LABEL_MAX_LENGTH = 32


class LabelEntry(BaseModel):
    """One label on a dossier — what the GET endpoint returns per row."""

    model_config = ConfigDict(extra="forbid")

    label: str
    created_by: str | None
    created_at: str


class LabelCatalogEntry(BaseModel):
    """One row in the global label catalog (label + how many dossiers carry it)."""

    model_config = ConfigDict(extra="forbid")

    label: str
    count: int
