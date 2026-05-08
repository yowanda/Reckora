"""Tests for the Wayback archive integration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from reckora.evidence.archive import (
    Archiver,
    WaybackArchiver,
    augment_traces_with_archive,
)
from reckora.evidence.chain import make_evidence
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


def _client_with(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler, follow_redirects=False)


@pytest.mark.asyncio
async def test_wayback_returns_snapshot_from_location_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/save/https://example.com/alice"
        return httpx.Response(
            302,
            headers={"Location": "/web/20260101000000/https://example.com/alice"},
        )

    transport = httpx.MockTransport(handler)
    async with _client_with(transport) as client:
        archiver = WaybackArchiver(client=client)
        result = await archiver.archive("https://example.com/alice")
    assert result == "https://web.archive.org/web/20260101000000/https://example.com/alice"


@pytest.mark.asyncio
async def test_wayback_accepts_absolute_location_header() -> None:
    snap = "https://web.archive.org/web/20260101000000/https://example.com/alice"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": snap})

    async with _client_with(httpx.MockTransport(handler)) as client:
        archiver = WaybackArchiver(client=client)
        result = await archiver.archive("https://example.com/alice")
    assert result == snap


@pytest.mark.asyncio
async def test_wayback_accepts_content_location_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Location": "/web/20260101000000/https://example.com/x"},
        )

    async with _client_with(httpx.MockTransport(handler)) as client:
        archiver = WaybackArchiver(client=client)
        result = await archiver.archive("https://example.com/x")
    assert result == "https://web.archive.org/web/20260101000000/https://example.com/x"


@pytest.mark.asyncio
async def test_wayback_returns_none_on_missing_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={})

    async with _client_with(httpx.MockTransport(handler)) as client:
        archiver = WaybackArchiver(client=client)
        assert await archiver.archive("https://example.com/x") is None


@pytest.mark.asyncio
async def test_wayback_returns_none_on_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated network failure")

    async with _client_with(httpx.MockTransport(handler)) as client:
        archiver = WaybackArchiver(client=client)
        assert await archiver.archive("https://example.com/x") is None


@pytest.mark.asyncio
async def test_wayback_owns_default_client_close_is_safe() -> None:
    archiver = WaybackArchiver()
    # No HTTP call here; we just want to make sure the default-client path
    # constructs and closes cleanly without raising.
    await archiver.aclose()


@pytest.mark.asyncio
async def test_wayback_supports_async_context_manager() -> None:
    async with WaybackArchiver() as archiver:
        assert isinstance(archiver, WaybackArchiver)


class _FakeArchiver:
    """Records each call and returns a deterministic snapshot URL."""

    def __init__(self, mapping: dict[str, str | None]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    async def archive(self, source_url: str) -> str | None:
        self.calls.append(source_url)
        return self.mapping.get(source_url)


@pytest.mark.asyncio
async def test_augment_dedupes_calls_per_source_url() -> None:
    snap = "https://web.archive.org/web/2026/https://example.com/alice"
    a = _trace("https://example.com/alice")
    b = _trace("https://example.com/alice", value="alice")
    c = _trace("https://example.com/bob", value="bob")
    archiver = _FakeArchiver(
        {
            "https://example.com/alice": snap,
            "https://example.com/bob": None,
        }
    )

    out = await augment_traces_with_archive([a, b, c], archiver)

    # One call per unique URL, regardless of trace count.
    assert sorted(archiver.calls) == [
        "https://example.com/alice",
        "https://example.com/bob",
    ]
    assert out[0].evidence.archive_url == snap
    assert out[1].evidence.archive_url == snap
    assert out[2].evidence.archive_url is None
    # Original traces are untouched (Evidence is frozen).
    assert a.evidence.archive_url is None


@pytest.mark.asyncio
async def test_augment_swallows_archiver_exceptions() -> None:
    class _Boom:
        async def archive(self, source_url: str) -> str | None:
            raise RuntimeError("upstream rejected")

    out = await augment_traces_with_archive([_trace("https://x/y")], _Boom())
    assert out[0].evidence.archive_url is None


@pytest.mark.asyncio
async def test_augment_no_traces_returns_input_unchanged() -> None:
    out: list[Trace] = await augment_traces_with_archive([], _FakeArchiver({}))
    assert out == []


def test_archiver_is_a_protocol() -> None:
    fake: Archiver = _FakeArchiver({})
    assert hasattr(fake, "archive")


@pytest.mark.asyncio
async def test_orchestrator_passes_archiver_through() -> None:
    from reckora.collectors.base import Collector
    from reckora.orchestrator import Orchestrator

    snap = "https://web.archive.org/web/2026/https://fake/alice"

    class _Coll(Collector):
        name = "fake"
        supported = frozenset({"username"})

        async def collect(self, identifier: Any) -> list[Trace]:
            return [_trace(f"https://fake/{identifier.value}", value=identifier.value)]

    archiver = _FakeArchiver({"https://fake/alice": snap})
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    subject, traces, _ = await Orchestrator([_Coll()]).investigate(seed, archiver=archiver)
    assert traces[0].evidence.archive_url == snap
    # Subject's identifiers list still has the original entry; no leakage.
    assert subject.seed_identifier == seed
