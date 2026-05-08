"""Tests for the GitHub API collector."""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.github_api import GITHUB_API_BASE, GitHubCollector
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource


@pytest.fixture
def alice_identifier() -> Identifier:
    return Identifier(type=IdentifierType.USERNAME, value="alice")


async def test_collect_404_returns_empty(
    httpx_mock: HTTPXMock, alice_identifier: Identifier
) -> None:
    httpx_mock.add_response(
        url=f"{GITHUB_API_BASE}/users/alice",
        status_code=404,
        json={"message": "Not Found"},
    )
    async with httpx.AsyncClient() as client:
        collector = GitHubCollector(client=client)
        traces = await collector.collect(alice_identifier)
    assert traces == []


async def test_collect_normalises_response(
    httpx_mock: HTTPXMock, alice_identifier: Identifier
) -> None:
    httpx_mock.add_response(
        url=f"{GITHUB_API_BASE}/users/alice",
        json={
            "login": "alice",
            "html_url": "https://github.com/alice",
            "name": "Alice A",
            "bio": "OSINT researcher",
            "avatar_url": "https://example.com/a.png",
            "location": "Jakarta",
            "company": "Acme",
            "blog": "https://alice.example",
            "email": None,
            "twitter_username": "alice",
            "followers": 42,
            "public_repos": 7,
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    )
    async with httpx.AsyncClient() as client:
        collector = GitHubCollector(client=client)
        traces = await collector.collect(alice_identifier)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.source == TraceSource.GITHUB_API
    assert trace.fields["platform"] == "github"
    assert trace.fields["display_name"] == "Alice A"
    assert trace.fields["bio"] == "OSINT researcher"
    assert trace.fields["avatar_url"] == "https://example.com/a.png"
    assert trace.fields["followers"] == 42
    assert trace.evidence.source_url == f"{GITHUB_API_BASE}/users/alice"
    assert len(trace.evidence.payload_sha256) == 64
    assert trace.evidence.raw_payload is None  # GitHub responses are dropped


async def test_collect_skips_unsupported_identifier() -> None:
    collector = GitHubCollector()
    ident = Identifier(type=IdentifierType.DOMAIN, value="example.com")
    traces = await collector.collect(ident)
    assert traces == []


async def test_token_added_to_headers(
    httpx_mock: HTTPXMock,
    alice_identifier: Identifier,
) -> None:
    httpx_mock.add_response(
        url=f"{GITHUB_API_BASE}/users/alice",
        json={"login": "alice"},
    )
    async with httpx.AsyncClient() as client:
        collector = GitHubCollector(client=client, token="ghp_test")
        await collector.collect(alice_identifier)
    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["Authorization"] == "Bearer ghp_test"
