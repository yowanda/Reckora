"""High-level investigation orchestrator.

The orchestrator is intentionally thin: it fans Identifiers out across the
collectors it was constructed with, runs the correlation engine over the
resulting Traces, and packages everything into a Subject + Edges tuple.

It deliberately does NOT:
- own a database (Phase 2 adds SQLite persistence behind a separate seam)
- own the AI reasoning layer (callers compose those when they want)
- expand the identifier set recursively (that is a Phase 4 concern, gated by
  confidence floors)
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable

from .collectors.base import Collector
from .correlation.engine import correlate
from .models.entity import Edge, Identifier, Subject, Trace

log = logging.getLogger(__name__)


class Orchestrator:
    """Runs the full collect -> correlate pipeline."""

    def __init__(self, collectors: Iterable[Collector]) -> None:
        self._collectors = list(collectors)

    async def investigate(
        self,
        seed: Identifier,
        *,
        extra_identifiers: list[Identifier] | None = None,
    ) -> tuple[Subject, list[Trace], list[Edge]]:
        """Collect Traces for `seed` (+ optional extras) and correlate them."""
        identifiers: list[Identifier] = [seed, *(extra_identifiers or [])]

        traces: list[Trace] = []
        for ident in identifiers:
            for collector in self._collectors:
                if not collector.supports(ident):
                    continue
                try:
                    traces.extend(await collector.collect(ident))
                except Exception:
                    log.exception(
                        "collector %s failed on %s",
                        collector.name,
                        ident,
                    )

        edges = correlate(traces)
        subject = Subject(
            id=f"subj-{uuid.uuid4().hex[:12]}",
            seed_identifier=seed,
            identifiers=identifiers,
            traces=traces,
        )
        return subject, traces, edges
