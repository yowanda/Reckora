"""Forensic screenshot capture for evidence URLs.

When enabled, each unique source URL collected during an investigation is
rendered headlessly and saved as a PNG. The path to that PNG is recorded as
``Evidence.screenshot_path`` so the dossier carries a frozen visual snapshot
alongside the canonical payload SHA-256.

Like archival, screenshot capture is intentionally **best-effort**:

- A failure to render (navigation timeout, missing browser binary, etc.) MUST
  NEVER fail an investigation. The trace stays valid; ``screenshot_path``
  simply remains ``None``.
- Calls are deduped by source URL inside
  :func:`augment_traces_with_screenshot` so we render each unique URL once.
- The screenshotter is plugged into the orchestrator behind the
  :class:`Screenshotter` Protocol so tests (and alternative implementations
  such as ``shot-scraper`` / ``puppeteer``) can swap it out without touching
  the seam.

The Playwright dependency is gated behind the optional ``[screenshots]``
extra so the default install stays slim:

.. code-block:: bash

    uv sync --extra screenshots
    playwright install chromium
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any, Protocol

from ..models.entity import Trace

log = logging.getLogger(__name__)


class Screenshotter(Protocol):
    """Anything that can turn a live URL into a frozen PNG path / URL."""

    async def screenshot(self, source_url: str) -> str | None:
        """Return the path (or URL) of the captured PNG, or ``None`` on
        best-effort failure."""
        ...


def _digest(source_url: str) -> str:
    """Stable filename digest for a URL — same input always lands at the same
    output path so repeated renders idempotently overwrite each other."""
    return hashlib.sha1(source_url.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


class PlaywrightScreenshotter:
    """Render pages with a headless Chromium via Playwright (best-effort).

    Playwright is a heavy optional dependency, so the import is deferred to
    construction time: importing this module is cheap and can't break for
    users who never enable the ``[screenshots]`` extra.

    Captured PNGs are named after the SHA-1 of the source URL so the same URL
    always lands at the same filename — useful when the same page is
    referenced by multiple traces.
    """

    def __init__(
        self,
        *,
        output_dir: Path | str = "screenshots",
        path_prefix: str | None = None,
        viewport_width: int = 1280,
        viewport_height: int = 800,
        timeout_seconds: float = 20.0,
        full_page: bool = True,
    ) -> None:
        try:
            import playwright.async_api  # noqa: F401  (probe only)
        except ImportError as exc:  # pragma: no cover - import-guard branch
            raise RuntimeError(
                "playwright is required for screenshot capture; install with "
                "`uv sync --extra screenshots && playwright install chromium`"
            ) from exc
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._path_prefix = path_prefix
        self._viewport = {"width": viewport_width, "height": viewport_height}
        self._timeout_ms = int(timeout_seconds * 1000)
        self._full_page = full_page
        self._lock = asyncio.Lock()
        self._pw: Any | None = None
        self._browser: Any | None = None

    async def _ensure_browser(self) -> Any:
        if self._browser is None:  # pragma: no cover - requires real browser
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch()
        return self._browser

    async def aclose(self) -> None:
        if self._browser is not None:  # pragma: no cover - requires real browser
            await self._browser.close()
            self._browser = None
        if self._pw is not None:  # pragma: no cover - requires real browser
            await self._pw.stop()
            self._pw = None

    async def __aenter__(self) -> PlaywrightScreenshotter:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def screenshot(self, source_url: str) -> str | None:  # pragma: no cover
        # Real Playwright rendering needs Chromium binaries which we don't
        # ship in CI — exercised manually via the [screenshots] extra and
        # the `reckora investigate --screenshot` flag.
        target = self._output_dir / f"{_digest(source_url)}.png"
        try:
            async with self._lock:
                browser = await self._ensure_browser()
                context = await browser.new_context(viewport=self._viewport)
                page = await context.new_page()
                await page.goto(
                    source_url,
                    timeout=self._timeout_ms,
                    wait_until="networkidle",
                )
                await page.screenshot(path=str(target), full_page=self._full_page)
                await context.close()
        except Exception as exc:
            # Best-effort by design — never fail an investigation because
            # Chromium choked on a single page.
            log.debug("screenshot failed for %s: %s", source_url, exc)
            return None
        if self._path_prefix is not None:
            return f"{self._path_prefix.rstrip('/')}/{target.name}"
        return str(target)


async def augment_traces_with_screenshot(
    traces: list[Trace],
    screenshotter: Screenshotter,
) -> list[Trace]:
    """Return a new list of Traces whose Evidence carries a screenshot path.

    Calls are deduplicated by ``Evidence.source_url`` so a page referenced by
    multiple traces is only rendered once. Trace ordering is preserved so
    downstream rendering is stable.
    """
    if not traces:
        return traces
    unique_urls = list({t.evidence.source_url for t in traces})
    results = await asyncio.gather(
        *(screenshotter.screenshot(u) for u in unique_urls),
        return_exceptions=True,
    )
    shot_by_url: dict[str, str | None] = {}
    for url, result in zip(unique_urls, results, strict=True):
        if isinstance(result, BaseException):
            log.debug("screenshotter raised for %s: %s", url, result)
            shot_by_url[url] = None
        else:
            shot_by_url[url] = result

    out: list[Trace] = []
    for t in traces:
        path = shot_by_url.get(t.evidence.source_url)
        if path is None:
            out.append(t)
            continue
        new_evidence = t.evidence.model_copy(update={"screenshot_path": path})
        out.append(t.model_copy(update={"evidence": new_evidence}))
    return out
