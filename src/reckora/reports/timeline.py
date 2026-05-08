"""Timeline reconstruction across collected Traces.

A timeline is the chronological re-projection of every Trace's evidence — one
entry per Trace, sorted by ``Evidence.fetched_at`` ascending. Renderers
embed the timeline in the dossier (markdown / HTML / PDF) and the JSON
exporter surfaces it as a top-level ``timeline`` array so API consumers can
re-render it without re-deriving the order from raw traces.

The timeline is always derived from ``Evidence.fetched_at`` because it is the
one timestamp every Trace carries (collectors are not required to surface
their own ``created_at`` / ``updated_at`` fields). Ties on ``fetched_at`` fall
back to the canonical ``payload_sha256`` so the output is deterministic
regardless of input order.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from ..models.entity import Trace
from ..models.enums import IdentifierType, TraceSource


class TimelineEntry(BaseModel):
    """A single chronological event projected from a :class:`Trace`."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    source: TraceSource
    identifier_type: IdentifierType
    identifier_value: str
    evidence_sha256: str
    source_url: str
    archive_url: str | None = None
    screenshot_path: str | None = None

    @property
    def evidence_sha256_short(self) -> str:
        """First 16 hex chars of ``evidence_sha256`` — matches the dossier shorthand."""
        return self.evidence_sha256[:16]


def build_timeline(traces: list[Trace]) -> list[TimelineEntry]:
    """Project ``traces`` onto a chronological timeline.

    Sorted ascending by ``Evidence.fetched_at``; ties are broken by
    ``Evidence.payload_sha256`` so equal timestamps still produce a stable
    order.
    """
    entries = [
        TimelineEntry(
            timestamp=t.evidence.fetched_at,
            source=t.source,
            identifier_type=t.identifier.type,
            identifier_value=t.identifier.value,
            evidence_sha256=t.evidence.payload_sha256,
            source_url=t.evidence.source_url,
            archive_url=t.evidence.archive_url,
            screenshot_path=t.evidence.screenshot_path,
        )
        for t in traces
    ]
    entries.sort(key=lambda e: (e.timestamp, e.evidence_sha256))
    return entries
