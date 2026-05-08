"""Display-name divergence rule.

If the same subject surfaces multiple distinct ``display_name`` values across
collectors, flag it as a low-severity anomaly. Reasons can be benign
(rebrand, locale-specific spelling) or hostile (sock-puppet, identity
collision) — the rule labels it but does not interpret it.

Comparison is case-insensitive and whitespace-insensitive so trivially
equivalent forms (``"Alice Wonder"`` vs ``"alice wonder"``) collapse to a
single canonical value.
"""

from __future__ import annotations

from datetime import datetime

from ...models.entity import Trace
from ..models import Anomaly, AnomalyKind, AnomalySeverity


def _canonicalise(name: str) -> str:
    return " ".join(name.split()).casefold()


def detect(traces: list[Trace], *, now: datetime) -> list[Anomaly]:
    """Return at most one Anomaly summarising display-name divergence."""
    del now
    seen: dict[str, str] = {}
    supporting: list[str] = []
    for trace in traces:
        raw = trace.fields.get("display_name")
        if not isinstance(raw, str):
            continue
        canonical = _canonicalise(raw)
        if not canonical:
            continue
        if canonical not in seen:
            seen[canonical] = raw.strip()
        supporting.append(trace.evidence.payload_sha256)

    if len(seen) < 2:
        return []

    rendered = ", ".join(repr(name) for name in sorted(seen.values()))
    return [
        Anomaly(
            kind=AnomalyKind.NAME_DIVERGENCE,
            severity=AnomalySeverity.LOW,
            message=(f"Display name diverges across {len(seen)} collector value(s): {rendered}."),
            supporting_evidence=supporting,
        )
    ]
