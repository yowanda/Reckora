"""Pydantic models for anomaly findings."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AnomalyKind(StrEnum):
    """The category of integrity issue an :class:`Anomaly` represents."""

    FUTURE_EVIDENCE = "future_evidence"
    """``Evidence.fetched_at`` is in the future relative to ``now`` — clock
    skew, fabricated payload, or a misconfigured collector."""

    TEMPORAL_INCONSISTENCY = "temporal_inconsistency"
    """The Trace's own timestamps disagree (``created_at > updated_at``, or
    ``created_at`` postdates ``Evidence.fetched_at``)."""

    EXPIRED_DOMAIN = "expired_domain"
    """A WHOIS / RDAP trace whose ``expires_at`` is in the past — the
    domain lapsed before we observed it."""

    INVALID_PHONE = "invalid_phone"
    """A phone Trace where libphonenumber reported ``is_valid=False`` —
    syntactically possible but not a real number."""

    NAME_DIVERGENCE = "name_divergence"
    """Multiple distinct ``display_name`` values surfaced for the same
    subject — could be a sock-puppet, an alias, or simply a relabel."""


class AnomalySeverity(StrEnum):
    """Triage tier — how loudly a renderer should advertise the finding."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Anomaly(BaseModel):
    """A single integrity finding emitted by an anomaly rule.

    ``supporting_evidence`` is the list of ``Evidence.payload_sha256`` hashes
    that justify the claim — same convention as :class:`reckora.models.entity.Edge`.
    Anomalies that depend on multiple Traces include each Trace's hash so
    every finding remains auditable.
    """

    model_config = ConfigDict(frozen=True)

    kind: AnomalyKind
    severity: AnomalySeverity
    message: str
    supporting_evidence: list[str] = Field(default_factory=list)
