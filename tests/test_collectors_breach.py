"""Tests for the HIBP breach-lookup collector.

The collector talks to ``haveibeenpwned.com`` so every test mocks the HTTP
layer with ``pytest-httpx`` — we never go to the network, and the fixtures
encode the parts of the v3 API contract we actually depend on.
"""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.breach import HIBP_API_BASE, BreachCollector
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource


@pytest.fixture
def email_alice() -> Identifier:
    return Identifier(type=IdentifierType.EMAIL, value="alice@example.com")


def _hibp_url(email: str) -> str:
    return f"{HIBP_API_BASE}/breachedaccount/{email}?truncateResponse=false&includeUnverified=true"


async def test_collect_skips_unsupported_identifier() -> None:
    collector = BreachCollector(api_key="dummy")
    ident = Identifier(type=IdentifierType.USERNAME, value="alice")
    assert await collector.collect(ident) == []


async def test_collect_no_api_key_short_circuits(email_alice: Identifier) -> None:
    """Without an API key the collector must NOT touch the network."""
    collector = BreachCollector(api_key=None)
    assert await collector.collect(email_alice) == []


async def test_collect_404_returns_clean_trace(
    httpx_mock: HTTPXMock, email_alice: Identifier
) -> None:
    """404 is HIBP's documented "no breaches" reply, NOT an error."""
    httpx_mock.add_response(url=_hibp_url("alice@example.com"), status_code=404)
    async with httpx.AsyncClient() as client:
        collector = BreachCollector(api_key="k", client=client)
        traces = await collector.collect(email_alice)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.source == TraceSource.BREACH_HIBP
    fields = trace.fields
    assert fields["email"] == "alice@example.com"
    assert fields["breach_count"] == 0
    assert fields["breaches"] == []
    assert fields["data_classes"] == []
    assert fields["has_sensitive_breach"] is False
    assert fields["first_breach_date"] is None
    assert fields["latest_breach_date"] is None


async def test_collect_normalises_breach_list(
    httpx_mock: HTTPXMock, email_alice: Identifier
) -> None:
    httpx_mock.add_response(
        url=_hibp_url("alice@example.com"),
        json=[
            {
                "Name": "Adobe",
                "Title": "Adobe",
                "Domain": "adobe.com",
                "BreachDate": "2013-10-04",
                "AddedDate": "2013-12-04T00:00:00Z",
                "PwnCount": 152_445_165,
                "DataClasses": ["Email addresses", "Password hints", "Passwords"],
                "IsVerified": True,
                "IsFabricated": False,
                "IsSensitive": False,
                "IsRetired": False,
                "IsSpamList": False,
            },
            {
                "Name": "LinkedIn",
                "Title": "LinkedIn",
                "Domain": "linkedin.com",
                "BreachDate": "2012-05-05",
                "AddedDate": "2016-05-21T00:00:00Z",
                "PwnCount": 164_611_595,
                "DataClasses": ["Email addresses", "Passwords"],
                "IsVerified": True,
                "IsFabricated": False,
                "IsSensitive": False,
                "IsRetired": False,
                "IsSpamList": False,
            },
        ],
    )
    async with httpx.AsyncClient() as client:
        collector = BreachCollector(api_key="k", client=client)
        traces = await collector.collect(email_alice)

    assert len(traces) == 1
    fields = traces[0].fields
    assert fields["email"] == "alice@example.com"
    assert fields["breach_count"] == 2
    # Sorted ascending by breach_date for deterministic dossier output.
    names = [b["name"] for b in fields["breaches"]]
    assert names == ["LinkedIn", "Adobe"]
    assert fields["first_breach_date"] == "2012-05-05"
    assert fields["latest_breach_date"] == "2013-10-04"
    assert fields["data_classes"] == sorted({"Email addresses", "Password hints", "Passwords"})
    assert fields["has_sensitive_breach"] is False
    # Per-breach payload exposes the high-signal HIBP fields verbatim.
    adobe = next(b for b in fields["breaches"] if b["name"] == "Adobe")
    assert adobe["domain"] == "adobe.com"
    assert adobe["pwn_count"] == 152_445_165
    assert adobe["data_classes"] == ["Email addresses", "Password hints", "Passwords"]
    assert adobe["is_verified"] is True
    assert adobe["is_sensitive"] is False


async def test_collect_flags_sensitive_breach(
    httpx_mock: HTTPXMock, email_alice: Identifier
) -> None:
    httpx_mock.add_response(
        url=_hibp_url("alice@example.com"),
        json=[
            {
                "Name": "SomeAdultSite",
                "BreachDate": "2020-01-01",
                "DataClasses": ["Email addresses"],
                "IsSensitive": True,
                "IsVerified": True,
            }
        ],
    )
    async with httpx.AsyncClient() as client:
        collector = BreachCollector(api_key="k", client=client)
        traces = await collector.collect(email_alice)

    assert traces[0].fields["has_sensitive_breach"] is True
    assert traces[0].fields["breaches"][0]["is_sensitive"] is True


async def test_collect_canonicalises_email_to_lowercase(
    httpx_mock: HTTPXMock,
) -> None:
    """Mixed-case input MUST hit the lowercase URL and surface lowercase email."""
    ident = Identifier(type=IdentifierType.EMAIL, value="Alice@Example.com")
    httpx_mock.add_response(url=_hibp_url("alice@example.com"), status_code=404)
    async with httpx.AsyncClient() as client:
        collector = BreachCollector(api_key="k", client=client)
        traces = await collector.collect(ident)
    assert traces[0].fields["email"] == "alice@example.com"


async def test_collect_ignores_non_dict_breach_entries(
    httpx_mock: HTTPXMock, email_alice: Identifier
) -> None:
    httpx_mock.add_response(
        url=_hibp_url("alice@example.com"),
        json=[
            "garbage",
            {"Name": "Adobe", "BreachDate": "2013-10-04", "DataClasses": []},
        ],
    )
    async with httpx.AsyncClient() as client:
        collector = BreachCollector(api_key="k", client=client)
        traces = await collector.collect(email_alice)
    assert traces[0].fields["breach_count"] == 1


async def test_collect_handles_non_list_payload(
    httpx_mock: HTTPXMock, email_alice: Identifier
) -> None:
    """An unexpected payload shape is treated as 'no breaches found'."""
    httpx_mock.add_response(url=_hibp_url("alice@example.com"), json={"oops": True})
    async with httpx.AsyncClient() as client:
        collector = BreachCollector(api_key="k", client=client)
        traces = await collector.collect(email_alice)
    assert len(traces) == 1
    assert traces[0].fields["breach_count"] == 0


async def test_collect_401_raises(httpx_mock: HTTPXMock, email_alice: Identifier) -> None:
    """A bad/missing key MUST surface so misconfig isn't silently swallowed."""
    httpx_mock.add_response(url=_hibp_url("alice@example.com"), status_code=401)
    async with httpx.AsyncClient() as client:
        collector = BreachCollector(api_key="k", client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await collector.collect(email_alice)


async def test_collect_sends_required_hibp_headers(
    httpx_mock: HTTPXMock, email_alice: Identifier
) -> None:
    httpx_mock.add_response(url=_hibp_url("alice@example.com"), status_code=404)
    async with httpx.AsyncClient() as client:
        collector = BreachCollector(api_key="my-key", client=client)
        await collector.collect(email_alice)

    request = httpx_mock.get_requests()[0]
    assert request.headers["hibp-api-key"] == "my-key"
    assert request.headers["User-Agent"] == "Reckora/0.1"
    assert request.headers["Accept"] == "application/json"


async def test_collect_evidence_drops_raw_payload(
    httpx_mock: HTTPXMock, email_alice: Identifier
) -> None:
    """Sensitive breach data must NOT be inlined into the evidence row."""
    httpx_mock.add_response(
        url=_hibp_url("alice@example.com"),
        json=[
            {
                "Name": "Adobe",
                "BreachDate": "2013-10-04",
                "DataClasses": ["Email addresses"],
            }
        ],
    )
    async with httpx.AsyncClient() as client:
        collector = BreachCollector(api_key="k", client=client)
        traces = await collector.collect(email_alice)

    evidence = traces[0].evidence
    assert evidence.raw_payload is None
    assert evidence.source_url == _hibp_url("alice@example.com")
    assert len(evidence.payload_sha256) == 64


def test_supports_only_email_identifier() -> None:
    collector = BreachCollector(api_key="k")
    assert collector.supports(Identifier(type=IdentifierType.EMAIL, value="a@b.co"))
    assert not collector.supports(Identifier(type=IdentifierType.USERNAME, value="a"))
    assert not collector.supports(Identifier(type=IdentifierType.PHONE, value="+1"))
