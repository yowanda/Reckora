"""Pydantic models for the Reckora entity graph."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import EdgeKind, IdentifierType, TraceSource


class Identifier(BaseModel):
    """An atomic public identifier — the thing we *can* observe.

    Identifiers are content-addressed: two `Identifier` instances with the same
    type and value are considered equal. The model is frozen so identifiers can
    be used as dict keys and set members across the engine.
    """

    model_config = ConfigDict(frozen=True)

    type: IdentifierType
    value: str

    def __str__(self) -> str:
        return f"{self.type.value}:{self.value}"


class Evidence(BaseModel):
    """A tamper-evident link between a Trace and the source it came from.

    `payload_sha256` is the canonical SHA-256 of the source payload. The raw
    payload is kept inline for small responses but may be elided (`None`) when
    a collector returns a large blob. The hash always survives.

    `archive_url` is an optional pointer to an out-of-band durable copy of the
    source page (e.g. an archive.org Wayback snapshot) so the chain remains
    auditable even if the live page disappears.
    """

    model_config = ConfigDict(frozen=True)

    source_url: str
    fetched_at: datetime
    payload_sha256: str
    raw_payload: dict[str, Any] | None = None
    archive_url: str | None = None


class Trace(BaseModel):
    """A single observed signal about an Identifier from a Collector.

    `fields` is the collector-normalised view of the source payload. The shape
    is intentionally loose so different collectors can surface different
    signals; correlation rules look up keys defensively.
    """

    identifier: Identifier
    source: TraceSource
    fields: dict[str, Any]
    evidence: Evidence


class Subject(BaseModel):
    """A *hypothesised* identity — the cluster of Identifiers we believe
    describe one person, plus the Traces that justify the belief.

    The id field is opaque and intentionally not derived from any identifier
    value: a Subject is a hypothesis, not a fact.
    """

    id: str
    seed_identifier: Identifier
    identifiers: list[Identifier] = Field(default_factory=list)
    traces: list[Trace] = Field(default_factory=list)


class Edge(BaseModel):
    """A confidence-scored relationship between two Identifiers.

    Each Edge carries a `confidence` in [0.0, 1.0], a `reasons` array
    explaining why the correlation engine emitted it, and a list of evidence
    SHA-256 hashes that support the claim. Downstream consumers (the AI
    reasoning layer, the report layer) MUST treat Edges as the only way to
    assert relationships — they are never inferred from raw fields.
    """

    source: Identifier
    target: Identifier
    kind: EdgeKind
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasons: list[str]
    supporting_evidence: list[str] = Field(default_factory=list)
