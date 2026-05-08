"""Tests for the WHOIS/RDAP collector."""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.whois_rdap import RDAP_DOMAIN_BASE, WhoisRdapCollector
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource


@pytest.fixture
def example_domain() -> Identifier:
    return Identifier(type=IdentifierType.DOMAIN, value="example.com")


async def test_collect_404_returns_empty(httpx_mock: HTTPXMock, example_domain: Identifier) -> None:
    httpx_mock.add_response(
        url=f"{RDAP_DOMAIN_BASE}/example.com",
        status_code=404,
    )
    async with httpx.AsyncClient() as client:
        collector = WhoisRdapCollector(client=client)
        traces = await collector.collect(example_domain)
    assert traces == []


async def test_collect_normalises_rdap(httpx_mock: HTTPXMock, example_domain: Identifier) -> None:
    httpx_mock.add_response(
        url=f"{RDAP_DOMAIN_BASE}/example.com",
        json={
            "ldhName": "example.com",
            "events": [
                {"eventAction": "registration", "eventDate": "2000-01-01T00:00:00Z"},
                {"eventAction": "expiration", "eventDate": "2030-01-01T00:00:00Z"},
                {"eventAction": "last changed", "eventDate": "2025-01-01T00:00:00Z"},
            ],
            "nameservers": [
                {"ldhName": "NS1.EXAMPLE.NET"},
                {"ldhName": "ns2.example.net"},
            ],
            "status": ["client transfer prohibited"],
            "entities": [
                {
                    "roles": ["registrar"],
                    "vcardArray": [
                        "vcard",
                        [
                            ["version", {}, "text", "4.0"],
                            ["fn", {}, "text", "Example Registrar Inc."],
                        ],
                    ],
                },
                {
                    "roles": ["registrant"],
                    "vcardArray": [
                        "vcard",
                        [
                            ["version", {}, "text", "4.0"],
                            ["org", {}, "text", "Example Org"],
                        ],
                    ],
                },
            ],
        },
    )
    async with httpx.AsyncClient() as client:
        collector = WhoisRdapCollector(client=client)
        traces = await collector.collect(example_domain)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.source == TraceSource.WHOIS_RDAP
    assert trace.fields["domain"] == "example.com"
    assert trace.fields["registrar"] == "Example Registrar Inc."
    assert trace.fields["registrant_org"] == "Example Org"
    assert trace.fields["created_at"] == "2000-01-01T00:00:00Z"
    assert trace.fields["expires_at"] == "2030-01-01T00:00:00Z"
    assert trace.fields["nameservers"] == ["ns1.example.net", "ns2.example.net"]
    assert trace.fields["status"] == ["client transfer prohibited"]


async def test_collect_skips_unsupported_identifier() -> None:
    collector = WhoisRdapCollector()
    ident = Identifier(type=IdentifierType.USERNAME, value="alice")
    assert await collector.collect(ident) == []


async def test_collect_handles_minimal_payload(
    httpx_mock: HTTPXMock,
    example_domain: Identifier,
) -> None:
    httpx_mock.add_response(
        url=f"{RDAP_DOMAIN_BASE}/example.com",
        json={"ldhName": "example.com"},
    )
    async with httpx.AsyncClient() as client:
        collector = WhoisRdapCollector(client=client)
        traces = await collector.collect(example_domain)
    assert len(traces) == 1
    fields = traces[0].fields
    assert fields["domain"] == "example.com"
    assert fields["registrar"] is None
    assert fields["registrant_org"] is None
    assert fields["nameservers"] == []
    assert fields["status"] == []
