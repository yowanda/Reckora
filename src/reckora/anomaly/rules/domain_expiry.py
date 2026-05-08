"""Expired-domain rule — flags WHOIS / RDAP traces whose registration lapsed."""

from __future__ import annotations

from datetime import datetime

from ...models.entity import Trace
from ...models.enums import TraceSource
from ..models import Anomaly, AnomalyKind, AnomalySeverity
from .temporal import _parse_iso


def detect(traces: list[Trace], *, now: datetime) -> list[Anomaly]:
    """Return one Anomaly per WHOIS / RDAP Trace whose ``expires_at`` is past."""
    del now  # we compare against ``Evidence.fetched_at`` so the rule is auditable
    findings: list[Anomaly] = []
    for trace in traces:
        if trace.source is not TraceSource.WHOIS_RDAP:
            continue
        expires = _parse_iso(trace.fields.get("expires_at"))
        if expires is None:
            continue
        fetched_at = trace.evidence.fetched_at
        if expires >= fetched_at:
            continue
        days_late = (fetched_at - expires).days
        domain = trace.fields.get("domain") or trace.identifier.value
        findings.append(
            Anomaly(
                kind=AnomalyKind.EXPIRED_DOMAIN,
                severity=AnomalySeverity.MEDIUM,
                message=(
                    f"`{domain}` expired on {expires.isoformat()} — "
                    f"{days_late} day(s) before the trace was collected at "
                    f"{fetched_at.isoformat()}."
                ),
                supporting_evidence=[trace.evidence.payload_sha256],
            )
        )
    return findings
