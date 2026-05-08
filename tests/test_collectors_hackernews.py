"""Tests for the Hacker News collector."""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.hackernews import (
    HACKERNEWS_API_BASE,
    HackerNewsCollector,
)
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource


@pytest.fixture
def pg_identifier() -> Identifier:
    return Identifier(type=IdentifierType.USERNAME, value="pg")


async def test_collect_404_returns_empty(httpx_mock: HTTPXMock, pg_identifier: Identifier) -> None:
    httpx_mock.add_response(
        url=f"{HACKERNEWS_API_BASE}/user/pg.json",
        status_code=404,
        json={"error": "not found"},
    )
    async with httpx.AsyncClient() as client:
        collector = HackerNewsCollector(client=client)
        traces = await collector.collect(pg_identifier)
    assert traces == []


async def test_collect_null_response_returns_empty(
    httpx_mock: HTTPXMock, pg_identifier: Identifier
) -> None:
    # HN's Firebase endpoint returns the literal JSON ``null`` (HTTP 200)
    # for accounts that do not exist. Treat that as a miss, not a crash.
    httpx_mock.add_response(
        url=f"{HACKERNEWS_API_BASE}/user/pg.json",
        content=b"null",
        headers={"Content-Type": "application/json"},
    )
    async with httpx.AsyncClient() as client:
        collector = HackerNewsCollector(client=client)
        traces = await collector.collect(pg_identifier)
    assert traces == []


async def test_collect_normalises_response(
    httpx_mock: HTTPXMock, pg_identifier: Identifier
) -> None:
    httpx_mock.add_response(
        url=f"{HACKERNEWS_API_BASE}/user/pg.json",
        json={
            "id": "pg",
            "created": 1173923446,
            "karma": 155111,
            "about": (
                'Bug fixer. Co-founder of <a href="https://ycombinator.com" '
                'rel="nofollow">Y Combinator</a>.'
            ),
            "submitted": [1, 2, 3, 4, 5],
            "delay": 0,
        },
    )
    async with httpx.AsyncClient() as client:
        collector = HackerNewsCollector(client=client)
        traces = await collector.collect(pg_identifier)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.source == TraceSource.HACKERNEWS_API
    assert trace.fields["platform"] == "hackernews"
    assert trace.fields["username"] == "pg"
    assert trace.fields["profile_url"] == "https://news.ycombinator.com/user?id=pg"
    assert trace.fields["bio"] == "Bug fixer. Co-founder of Y Combinator."
    assert "<a href" in (trace.fields["bio_html"] or "")
    assert trace.fields["karma"] == 155111
    assert trace.fields["submission_count"] == 5
    assert trace.fields["created_at"] == "2007-03-15T01:50:46+00:00"
    assert trace.fields["is_active"] is True
    assert trace.evidence.source_url == f"{HACKERNEWS_API_BASE}/user/pg.json"
    assert len(trace.evidence.payload_sha256) == 64
    assert trace.evidence.raw_payload is None  # HN responses are dropped


async def test_collect_empty_account_marked_inactive(
    httpx_mock: HTTPXMock,
) -> None:
    # An HN account that registered but never posted has no ``submitted``
    # array and the default 1 karma. Surface a trace so the absence of
    # activity is itself a finding, but flag ``is_active=False``.
    httpx_mock.add_response(
        url=f"{HACKERNEWS_API_BASE}/user/ghost.json",
        json={
            "id": "ghost",
            "created": 1700000000,
            "karma": 1,
            "about": None,
            "delay": 0,
        },
    )
    async with httpx.AsyncClient() as client:
        collector = HackerNewsCollector(client=client)
        traces = await collector.collect(Identifier(type=IdentifierType.USERNAME, value="ghost"))

    assert len(traces) == 1
    trace = traces[0]
    assert trace.fields["is_active"] is False
    assert trace.fields["bio"] is None
    assert trace.fields["bio_html"] is None
    assert trace.fields["karma"] == 1
    assert trace.fields["submission_count"] is None


async def test_collect_skips_unsupported_identifier() -> None:
    collector = HackerNewsCollector()
    ident = Identifier(type=IdentifierType.DOMAIN, value="example.com")
    traces = await collector.collect(ident)
    assert traces == []


@pytest.mark.parametrize(
    "value",
    [
        # Too long for HN (max 15 chars).
        "thisusernameiswaytoolong",
        # Contains characters HN never accepts.
        "alice@example",
        # Single character — HN requires at least 2.
        "a",
        # Wallet / hex / base58 strings that ride on USERNAME from upstream
        # callers should never produce a network request.
        "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
    ],
)
async def test_collect_rejects_non_hn_username_shapes(value: str) -> None:
    # Pre-filtering keeps the orchestrator from spending a request on
    # obvious non-HN strings (Bitcoin addresses, Ethereum hex, emails, ...).
    collector = HackerNewsCollector()
    traces = await collector.collect(Identifier(type=IdentifierType.USERNAME, value=value))
    assert traces == []


async def test_collect_uses_canonical_id_when_response_disagrees(
    httpx_mock: HTTPXMock,
) -> None:
    # HN normalises the ``id`` field server-side; if the requested value
    # differs only by casing, surface the server-canonical form so
    # downstream identifier joins stay consistent.
    httpx_mock.add_response(
        url=f"{HACKERNEWS_API_BASE}/user/Pg.json",
        json={
            "id": "pg",
            "created": 1173923446,
            "karma": 100,
            "submitted": [1],
        },
    )
    async with httpx.AsyncClient() as client:
        collector = HackerNewsCollector(client=client)
        traces = await collector.collect(Identifier(type=IdentifierType.USERNAME, value="Pg"))
    assert len(traces) == 1
    assert traces[0].fields["username"] == "pg"
    assert traces[0].fields["profile_url"] == "https://news.ycombinator.com/user?id=pg"
