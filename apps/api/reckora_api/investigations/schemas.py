"""Pydantic request / response schemas for investigation endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

LlmProvider = Literal["auto", "openai", "chatgpt_oauth", "agentrouter"]


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
    ai_iterations: int = Field(
        default=0,
        ge=0,
        le=10,
        description=(
            "Number of recursive AgentLoop rounds. 0 (default) = passive "
            "summary only. >=1 lets the LLM propose follow-up identifiers, "
            "verify them, and re-correlate, expanding the dossier graph."
        ),
    )
    ai_tools: bool = Field(
        default=False,
        description=(
            "When ai_iterations >= 1, allow the AgentLoop's LLM to call "
            "web_search and fetch_url so it can gather evidence beyond "
            "what the rule-based collectors found. Works under both "
            "OPENAI_API_KEY (chat-completions function calling) and "
            "ChatGPT OAuth (Responses-API function calling)."
        ),
    )
    ai_tool_calls: int = Field(
        default=8,
        ge=1,
        le=32,
        description="Per-iteration tool-call budget when ai_tools is true.",
    )
    breach: bool = Field(
        default=False,
        description=(
            "Enable the Have I Been Pwned breach-lookup collector for "
            "email identifiers (requires HIBP_API_KEY; off by default)."
        ),
    )
    anchor: bool = Field(
        default=False,
        description=(
            "Compute a cross-trace Merkle root and submit it to public "
            "OpenTimestamps calendars for tamper-evident timestamping "
            "(requires network access to the calendar fleet; off by default)."
        ),
    )
    llm_provider: LlmProvider = Field(
        default="auto",
        description=(
            "LLM backend used when ai=true. 'auto' (default) tries "
            "OPENAI_API_KEY first, then ChatGPT OAuth. 'openai' / "
            "'chatgpt_oauth' / 'agentrouter' pin the request to one "
            "path. The AgentRouter path uses the per-user BYOK key "
            "saved on the account, falling back to AGENTROUTER_API_KEY."
        ),
    )


class SubjectSummary(BaseModel):
    """List-row payload returned by ``GET /api/v1/subjects``.

    ``owner_username`` is ``None`` for legacy un-owned dossiers (created
    via the CLI before RBAC landed, or before being claimed by an
    admin). The frontend renders these as "system" rows.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    seed: IdentifierIn
    created_at: datetime
    identifier_count: int
    trace_count: int
    edge_count: int
    has_summary: bool
    has_hypotheses: bool
    has_anchor: bool = False
    owner_username: str | None = None


class SavedDossierPayload(BaseModel):
    """Full dossier returned by ``GET /api/v1/subjects/{id}``.

    Shape matches :func:`reckora.reports.json_export.to_dossier_dict` so the
    frontend can reuse the same TypeScript type for both endpoints.
    ``owner_username`` is omitted (``None``) for un-owned dossiers; see
    :class:`SubjectSummary` for the rationale.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    created_at: datetime
    subject: dict[str, Any]
    traces: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    anomalies: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    ai: dict[str, Any]
    anchor: dict[str, Any] | None = None
    owner_username: str | None = None
