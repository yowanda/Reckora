"""Pydantic request / response schemas for investigation endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IdentifierIn(BaseModel):
    """An identifier the client wants the orchestrator to consider."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(..., description="Identifier type, e.g. username, domain, url, email.")
    value: str = Field(..., min_length=1, max_length=512)


class InvestigationRequest(BaseModel):
    """Body for ``POST /api/v1/investigations``."""

    model_config = ConfigDict(extra="forbid")

    seed: IdentifierIn
    extras: list[IdentifierIn] = Field(default_factory=list)
    archive: bool = Field(
        default=False,
        description="Mint a Wayback Machine snapshot per evidence URL (best-effort, slow).",
    )
    screenshot: bool = Field(
        default=False,
        description=(
            "Capture a forensic PNG of each evidence URL via headless Chromium "
            "(requires the 'screenshots' extra; off by default)."
        ),
    )
    ai: bool = Field(
        default=False,
        description="Run the LLM reasoning layer (summary + hypotheses).",
    )
    breach: bool = Field(
        default=False,
        description=(
            "Enable the Have I Been Pwned breach-lookup collector for "
            "email identifiers (requires HIBP_API_KEY; off by default)."
        ),
    )


class SubjectSummary(BaseModel):
    """List-row payload returned by ``GET /api/v1/subjects``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    seed: IdentifierIn
    created_at: datetime
    identifier_count: int
    trace_count: int
    edge_count: int
    has_summary: bool
    has_hypotheses: bool


class SavedDossierPayload(BaseModel):
    """Full dossier returned by ``GET /api/v1/subjects/{id}``.

    Shape matches :func:`reckora.reports.json_export.to_dossier_dict` so the
    frontend can reuse the same TypeScript type for both endpoints.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    created_at: datetime
    subject: dict[str, Any]
    traces: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    ai: dict[str, Any]
