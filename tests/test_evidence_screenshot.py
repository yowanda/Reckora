"""Tests for the forensic screenshot integration.

Playwright is an optional, browser-binary-heavy dependency, so the suite
exercises the seam (Protocol + dedupe + augmentation + orchestrator wiring)
through a fake screenshotter instead of standing up a real Chromium.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from reckora.evidence.chain import make_evidence
from reckora.evidence.screenshot import (
    Screenshotter,
    augment_traces_with_screenshot,
)
from reckora.models.entity import Identifier, Trace
from reckora.models.enums import IdentifierType, TraceSource


def _trace(source_url: str, value: str = "alice") -> Trace:
    ident = Identifier(type=IdentifierType.USERNAME, value=value)
    fetched = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    return Trace(
        identifier=ident,
        source=TraceSource.WEB_PROFILE,
        fields={"platform": "fake"},
        evidence=make_evidence(source_url, {"login": value}, fetched_at=fetched),
    )


class _FakeScreenshotter:
    """Records each call and returns a deterministic path / URL."""

    def __init__(self, mapping: dict[str, str | None]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    async def screenshot(self, source_url: str) -> str | None:
        self.calls.append(source_url)
        return self.mapping.get(source_url)


@pytest.mark.asyncio
async def test_augment_dedupes_calls_per_source_url() -> None:
    shot = "/screenshots/abc123.png"
    a = _trace("https://example.com/alice")
    b = _trace("https://example.com/alice", value="alice")
    c = _trace("https://example.com/bob", value="bob")
    shotter = _FakeScreenshotter(
        {
            "https://example.com/alice": shot,
            "https://example.com/bob": None,
        }
    )

    out = await augment_traces_with_screenshot([a, b, c], shotter)

    # One call per unique URL, regardless of trace count.
    assert sorted(shotter.calls) == [
        "https://example.com/alice",
        "https://example.com/bob",
    ]
    assert out[0].evidence.screenshot_path == shot
    assert out[1].evidence.screenshot_path == shot
    assert out[2].evidence.screenshot_path is None
    # Original trace is untouched (Evidence is frozen).
    assert a.evidence.screenshot_path is None


@pytest.mark.asyncio
async def test_augment_swallows_screenshotter_exceptions() -> None:
    class _Boom:
        async def screenshot(self, source_url: str) -> str | None:
            raise RuntimeError("playwright crashed")

    out = await augment_traces_with_screenshot([_trace("https://x/y")], _Boom())
    assert out[0].evidence.screenshot_path is None


@pytest.mark.asyncio
async def test_augment_no_traces_returns_input_unchanged() -> None:
    out: list[Trace] = await augment_traces_with_screenshot([], _FakeScreenshotter({}))
    assert out == []


def test_screenshotter_is_a_protocol() -> None:
    fake: Screenshotter = _FakeScreenshotter({})
    assert hasattr(fake, "screenshot")


@pytest.mark.asyncio
async def test_orchestrator_passes_screenshotter_through() -> None:
    from reckora.collectors.base import Collector
    from reckora.orchestrator import Orchestrator

    shot = "/screenshots/alice.png"

    class _Coll(Collector):
        name = "fake"
        supported = frozenset({"username"})

        async def collect(self, identifier: Any) -> list[Trace]:
            return [_trace(f"https://fake/{identifier.value}", value=identifier.value)]

    shotter = _FakeScreenshotter({"https://fake/alice": shot})
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    subject, traces, _ = await Orchestrator([_Coll()]).investigate(
        seed,
        screenshotter=shotter,
    )
    assert traces[0].evidence.screenshot_path == shot
    assert subject.seed_identifier == seed


@pytest.mark.asyncio
async def test_orchestrator_runs_archiver_and_screenshotter_together() -> None:
    """Sanity check that both augmenters compose without dropping fields."""
    from reckora.collectors.base import Collector
    from reckora.orchestrator import Orchestrator

    shot = "/screenshots/alice.png"
    snap = "https://web.archive.org/web/2026/https://fake/alice"

    class _Arch:
        async def archive(self, source_url: str) -> str | None:
            return snap

    class _Coll(Collector):
        name = "fake"
        supported = frozenset({"username"})

        async def collect(self, identifier: Any) -> list[Trace]:
            return [_trace(f"https://fake/{identifier.value}", value=identifier.value)]

    shotter = _FakeScreenshotter({"https://fake/alice": shot})
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    _, traces, _ = await Orchestrator([_Coll()]).investigate(
        seed,
        archiver=_Arch(),
        screenshotter=shotter,
    )
    assert traces[0].evidence.archive_url == snap
    assert traces[0].evidence.screenshot_path == shot
