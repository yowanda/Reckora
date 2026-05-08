"""Anomaly-detection engine — fan a Trace list across every rule."""

from __future__ import annotations

from datetime import UTC, datetime

from ..models.entity import Trace
from .models import Anomaly, AnomalyKind, AnomalySeverity
from .rules import domain_expiry, name_divergence, phone_validity, temporal

_SEVERITY_RANK = {
    AnomalySeverity.HIGH: 0,
    AnomalySeverity.MEDIUM: 1,
    AnomalySeverity.LOW: 2,
}

_KIND_RANK = {kind: idx for idx, kind in enumerate(AnomalyKind)}


def detect_anomalies(
    traces: list[Trace],
    *,
    now: datetime | None = None,
) -> list[Anomaly]:
    """Return every anomaly across ``traces``, sorted by severity then kind.

    Findings are sorted ``HIGH`` -> ``MEDIUM`` -> ``LOW`` so a renderer can
    emit them in the order a reviewer wants to read. ``now`` is injectable
    for deterministic tests; production callers leave it ``None`` and get
    ``datetime.now(UTC)``.
    """
    when = now if now is not None else datetime.now(UTC)
    findings: list[Anomaly] = []
    for rule in (temporal, domain_expiry, phone_validity, name_divergence):
        findings.extend(rule.detect(traces, now=when))
    findings.sort(
        key=lambda a: (
            _SEVERITY_RANK[a.severity],
            _KIND_RANK[a.kind],
            a.message,
        )
    )
    return findings
