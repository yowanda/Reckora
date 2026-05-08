"""Wayback Machine snapshot integration for the evidence chain.

Reckora's value prop is *verifiable* evidence: every claim should still be
auditable when the upstream source changes or disappears. This module asks
the Internet Archive's Wayback Machine to mint a snapshot for each source URL
and surfaces the resulting permalink as ``Evidence.archive_url``.

Archival is intentionally **best-effort**:

- A failure to reach Wayback (timeout, non-2xx, missing redirect) MUST NEVER
  fail an investigation. The trace stays valid; ``archive_url`` simply
  remains ``None``.
- Calls are deduped by source URL inside :func:`augment_traces_with_archive`
  so we hit the Wayback API once per unique URL.
- The archiver is plugged into the orchestrator behind the
  :class:`Archiver` Protocol so tests (and future implementations such as
  ``conifer`` / ``archivebox``) can swap it out without touching the seam.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

import httpx

from ..models.entity import Trace

log = logging.getLogger(__name__)

_SAVE_BASE = "https://web.archive.org/save/"
_WAYBACK_PREFIX = "https://web.archive.org"


class Archiver(Protocol):
    """Anything that can turn a live URL into a durable archive URL."""

    async def archive(self, source_url: str) -> str | None:
        """Return a snapshot URL, or ``None`` on best-effort failure."""
        ...


class WaybackArchiver:
    """Save a URL to the Wayback Machine via Save Page Now.

    The class owns its own ``httpx.AsyncClient`` unless one is injected (which
    keeps the test suite hermetic — see ``tests/test_evidence_archive.py``).
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        user_agent: str = "reckora-archiver/0.1",
    ) -> None:
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": user_agent},
            follow_redirects=False,
        )
        self._timeout = timeout

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def __aenter__(self) -> WaybackArchiver:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def archive(self, source_url: str) -> str | None:
        save_url = f"{_SAVE_BASE}{source_url}"
        try:
            resp = await self._client.get(save_url, timeout=self._timeout)
        except httpx.HTTPError as exc:
            log.debug("wayback archive failed for %s: %s", source_url, exc)
            return None

        # Save Page Now responds with a 30x whose Location points at the new
        # snapshot. When the snapshot already exists or the call short-circuits
        # we may also get a 200 with Content-Location set.
        for header in ("Location", "Content-Location"):
            value: str | None = resp.headers.get(header)
            if not value:
                continue
            if value.startswith("/web/"):
                return f"{_WAYBACK_PREFIX}{value}"
            if value.startswith(f"{_WAYBACK_PREFIX}/web/"):
                return value
        log.debug(
            "wayback archive returned %d with no usable header for %s",
            resp.status_code,
            source_url,
        )
        return None


async def augment_traces_with_archive(
    traces: list[Trace],
    archiver: Archiver,
) -> list[Trace]:
    """Return a new list of Traces whose Evidence carries an archive URL.

    Calls are deduplicated by ``Evidence.source_url`` so a page referenced by
    multiple traces is only archived once. Trace ordering is preserved so
    downstream rendering is stable.
    """
    if not traces:
        return traces
    unique_urls = list({t.evidence.source_url for t in traces})
    results = await asyncio.gather(
        *(archiver.archive(u) for u in unique_urls),
        return_exceptions=True,
    )
    archive_by_url: dict[str, str | None] = {}
    for url, result in zip(unique_urls, results, strict=True):
        if isinstance(result, BaseException):
            log.debug("archiver raised for %s: %s", url, result)
            archive_by_url[url] = None
        else:
            archive_by_url[url] = result

    out: list[Trace] = []
    for t in traces:
        snapshot = archive_by_url.get(t.evidence.source_url)
        if snapshot is None:
            out.append(t)
            continue
        new_evidence = t.evidence.model_copy(update={"archive_url": snapshot})
        out.append(t.model_copy(update={"evidence": new_evidence}))
    return out
