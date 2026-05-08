"""Temporal-integrity rules.

Three findings live here, all driven by timestamps the chain already carries:

* :data:`AnomalyKind.FUTURE_EVIDENCE` — ``Evidence.fetched_at`` is in the
  future relative to ``now``. Either the local clock is skewed, the payload
  was fabricated, or the collector lied about when it ran.
* :data:`AnomalyKind.TEMPORAL_INCONSISTENCY` — the Trace's own
  ``created_at`` / ``updated_at`` fields disagree (``created_at`` is later
  than ``updated_at``), or ``created_at`` is later than the moment we
  observed the Trace.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ...models.entity import Trace
from ..models import Anomaly, AnomalyKind, AnomalySeverity


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 string into an aware UTC datetime, or return ``None``.

    Accepts both ``"...Z"`` (GitHub style) and ``"...+00:00"`` (RDAP style).
    Returns ``None`` for non-strings, blank strings, or anything ``fromisoformat``
    can't make sense of — anomaly rules must never crash on collector noise.
    """
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def detect(traces: list[Trace], *, now: datetime) -> list[Anomaly]:
    """Return temporal anomalies discovered across ``traces``."""
    findings: list[Anomaly] = []
    for trace in traces:
        sha = trace.evidence.payload_sha256
        fetched_at = trace.evidence.fetched_at

        if fetched_at > now:
            findings.append(
                Anomaly(
                    kind=AnomalyKind.FUTURE_EVIDENCE,
                    severity=AnomalySeverity.HIGH,
                    message=(
                        f"`{trace.source.value}` evidence claims to have been fetched at "
                        f"{fetched_at.isoformat()}, which is in the future relative to "
                        f"{now.isoformat()} — clock skew or fabricated payload."
                    ),
                    supporting_evidence=[sha],
                )
            )

        created = _parse_iso(trace.fields.get("created_at"))
        updated = _parse_iso(trace.fields.get("updated_at"))

        if created is not None and updated is not None and created > updated:
            findings.append(
                Anomaly(
                    kind=AnomalyKind.TEMPORAL_INCONSISTENCY,
                    severity=AnomalySeverity.HIGH,
                    message=(
                        f"`{trace.source.value}` reports `created_at` "
                        f"({created.isoformat()}) after `updated_at` "
                        f"({updated.isoformat()}) — impossible timeline."
                    ),
                    supporting_evidence=[sha],
                )
            )

        if created is not None and created > fetched_at:
            findings.append(
                Anomaly(
                    kind=AnomalyKind.TEMPORAL_INCONSISTENCY,
                    severity=AnomalySeverity.HIGH,
                    message=(
                        f"`{trace.source.value}` reports `created_at` "
                        f"({created.isoformat()}) after the evidence was observed "
                        f"({fetched_at.isoformat()})."
                    ),
                    supporting_evidence=[sha],
                )
            )

    return findings
