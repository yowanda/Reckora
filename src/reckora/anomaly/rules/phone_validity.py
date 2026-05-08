"""Phone-validity rule — surfaces phone Traces libphonenumber rejected."""

from __future__ import annotations

from datetime import datetime

from ...models.entity import Trace
from ...models.enums import TraceSource
from ..models import Anomaly, AnomalyKind, AnomalySeverity


def detect(traces: list[Trace], *, now: datetime) -> list[Anomaly]:
    """Flag every phone Trace whose ``is_valid`` field is explicitly ``False``."""
    del now  # purely structural — no time-of-evaluation needed
    findings: list[Anomaly] = []
    for trace in traces:
        if trace.source is not TraceSource.PHONE_LIBPHONENUMBER:
            continue
        is_valid = trace.fields.get("is_valid")
        if is_valid is not False:
            continue
        e164 = trace.fields.get("e164") or trace.identifier.value
        is_possible = trace.fields.get("is_possible")
        possible_clause = (
            "syntactically possible but unallocated"
            if is_possible
            else "neither valid nor syntactically possible"
        )
        findings.append(
            Anomaly(
                kind=AnomalyKind.INVALID_PHONE,
                severity=AnomalySeverity.MEDIUM,
                message=(
                    f"`{e164}` is {possible_clause} — libphonenumber rejected "
                    f"the number as invalid."
                ),
                supporting_evidence=[trace.evidence.payload_sha256],
            )
        )
    return findings
