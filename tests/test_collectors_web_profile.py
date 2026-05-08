"""Tests for the generic web profile collector."""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.web_profile import WebProfileCollector
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource


@pytest.fixture
def alice_url() -> Identifier:
    return Identifier(type=IdentifierType.URL, value="https://example.org/@alice")


async def test_extracts_og_tags(httpx_mock: HTTPXMock, alice_url: Identifier) -> None:
    body = """
    <html>
    <head>
      <title>Alice on Example</title>
      <meta property="og:title" content="Alice A" />
      <meta property="og:description" content="OSINT researcher and incident response." />
      <meta property="og:image" content="https://example.org/avatar/alice.png" />
      <meta property="og:site_name" content="Example" />
      <meta property="og:type" content="profile" />
    </head>
    <body></body>
    </html>
    """
    httpx_mock.add_response(url=alice_url.value, text=body, status_code=200)
    async with httpx.AsyncClient() as client:
        collector = WebProfileCollector(client=client)
        traces = await collector.collect(alice_url)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.source == TraceSource.WEB_PROFILE
    assert trace.fields["display_name"] == "Alice A"
    assert trace.fields["bio"] == "OSINT researcher and incident response."
    assert trace.fields["avatar_url"] == "https://example.org/avatar/alice.png"
    assert trace.fields["platform"] == "Example"
    assert trace.fields["og_type"] == "profile"


async def test_falls_back_to_title_when_no_og(httpx_mock: HTTPXMock, alice_url: Identifier) -> None:
    body = "<html><head><title>Alice on Example</title></head><body></body></html>"
    httpx_mock.add_response(url=alice_url.value, text=body, status_code=200)
    async with httpx.AsyncClient() as client:
        collector = WebProfileCollector(client=client)
        traces = await collector.collect(alice_url)
    assert len(traces) == 1
    assert traces[0].fields["display_name"] == "Alice on Example"
    assert traces[0].fields["bio"] is None
    assert traces[0].fields["platform"] == "example.org"


async def test_4xx_returns_empty(httpx_mock: HTTPXMock, alice_url: Identifier) -> None:
    httpx_mock.add_response(url=alice_url.value, status_code=404, text="not found")
    async with httpx.AsyncClient() as client:
        collector = WebProfileCollector(client=client)
        assert await collector.collect(alice_url) == []


async def test_unsupported_identifier_returns_empty() -> None:
    collector = WebProfileCollector()
    ident = Identifier(type=IdentifierType.USERNAME, value="alice")
    assert await collector.collect(ident) == []
