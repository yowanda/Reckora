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
from .evidence.archive import Archiver, augment_traces_with_archive
from .evidence.screenshot import Screenshotter, augment_traces_with_screenshot
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
        extra_collectors: Iterable[Collector] | None = None,
        archiver: Archiver | None = None,
        screenshotter: Screenshotter | None = None,
    ) -> tuple[Subject, list[Trace], list[Edge]]:
        """Collect Traces for `seed` (+ optional extras) and correlate them.

        ``extra_collectors`` is appended to the orchestrator's permanent
        collector list for the duration of this single call. Used by feature-
        flagged collectors (e.g. the HIBP breach lookup) that should only run
        when the caller explicitly opts in via ``--breach`` / ``breach: true``.

        When ``archiver`` is provided, every unique source URL across the
        collected traces is archived (best-effort) and the resulting snapshot
        URL is attached to each trace's :class:`Evidence.archive_url`.

        When ``screenshotter`` is provided, every unique source URL is also
        rendered to a PNG (best-effort) and the resulting path is attached to
        each trace's :class:`Evidence.screenshot_path`.
        """
        identifiers: list[Identifier] = [seed, *(extra_identifiers or [])]
        collectors: list[Collector] = [*self._collectors, *(extra_collectors or [])]

        traces: list[Trace] = []
        for ident in identifiers:
            for collector in collectors:
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

        if archiver is not None:
            traces = await augment_traces_with_archive(traces, archiver)
        if screenshotter is not None:
            traces = await augment_traces_with_screenshot(traces, screenshotter)

        edges = correlate(traces)
        subject = Subject(
            id=f"subj-{uuid.uuid4().hex[:12]}",
            seed_identifier=seed,
            identifiers=identifiers,
            traces=traces,
        )
        return subject, traces, edges
