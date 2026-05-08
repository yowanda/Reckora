"""Tests for the public email-profile collector.

The collector touches two networks: a DNS resolver for MX records and the
Gravatar HTTP API. Both are mocked here — DNS via a stub
:class:`dns.asyncresolver.Resolver`, HTTP via ``pytest-httpx`` — so the
suite is hermetic and the assertions can encode the wire formats we
actually depend on.
"""

from __future__ import annotations

import hashlib
from typing import Any, cast

import dns.asyncresolver
import dns.exception
import dns.resolver
import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.email import (
    GRAVATAR_API_BASE,
    EmailCollector,
    validate_syntax,
)
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource


@pytest.fixture
def email_alice() -> Identifier:
    return Identifier(type=IdentifierType.EMAIL, value="alice@example.com")


def _gravatar_url(email: str) -> str:
    digest = hashlib.md5(email.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"{GRAVATAR_API_BASE}/{digest}.json"


class _StubMxRdata:
    """Minimal stand-in for ``dns.rdtypes.IN.MX.MX`` answer rows."""

    def __init__(self, host: str) -> None:
        self.exchange = _StubName(host)


class _StubName:
    def __init__(self, host: str) -> None:
        self._host = host

    def to_text(self) -> str:
        return self._host


class _StubResolver:
    """In-memory resolver keyed on (qname, qtype).

    ``raises`` overrides the answer with a ``DNSException`` subclass so
    individual tests can simulate NXDOMAIN, NoAnswer, Timeout, etc.
    """

    def __init__(
        self,
        answers: dict[tuple[str, str], list[_StubMxRdata]],
        raises: Exception | None = None,
    ) -> None:
        self._answers = answers
        self._raises = raises

    async def resolve(self, qname: str, rdtype: str) -> list[_StubMxRdata]:
        if self._raises is not None:
            raise self._raises
        return self._answers[(qname, rdtype)]


def _stub(
    answers: dict[tuple[str, str], list[_StubMxRdata]] | None = None,
    *,
    raises: Exception | None = None,
) -> dns.asyncresolver.Resolver:
    """Build a stub resolver and cast it to the type ``EmailCollector`` expects.

    The stub only implements the one ``resolve()`` coroutine the collector
    actually calls; ``cast`` keeps mypy happy without forcing the stub to
    re-implement the full :class:`dns.asyncresolver.Resolver` API surface.
    """
    return cast(
        "dns.asyncresolver.Resolver",
        _StubResolver(answers or {}, raises=raises),
    )


# --- supports() ------------------------------------------------------------


def test_supports_only_email_identifier() -> None:
    collector = EmailCollector()
    assert collector.supports(Identifier(type=IdentifierType.EMAIL, value="a@b.co"))
    assert not collector.supports(Identifier(type=IdentifierType.USERNAME, value="a"))
    assert not collector.supports(Identifier(type=IdentifierType.PHONE, value="+1"))


async def test_collect_skips_unsupported_identifier() -> None:
    collector = EmailCollector()
    ident = Identifier(type=IdentifierType.USERNAME, value="alice")
    assert await collector.collect(ident) == []


# --- syntax validator ------------------------------------------------------


@pytest.mark.parametrize(
    "email",
    [
        "alice@example.com",
        "a.b+tag@sub.example.co.uk",
        "user_42@host.io",
    ],
)
def test_validate_syntax_accepts_well_formed(email: str) -> None:
    assert validate_syntax(email) is True


@pytest.mark.parametrize(
    "email",
    [
        "",
        "no-at-sign",
        "double@@example.com",
        "spaces in@example.com",
        "missing-tld@example",
        "@example.com",
        "alice@",
    ],
)
def test_validate_syntax_rejects_garbage(email: str) -> None:
    assert validate_syntax(email) is False


async def test_collect_invalid_syntax_emits_terse_trace_no_network() -> None:
    """Garbage seed: emit a syntax_invalid trace, do NOT touch the network."""
    ident = Identifier(type=IdentifierType.EMAIL, value="not-an-email")
    collector = EmailCollector(resolver=_stub())
    traces = await collector.collect(ident)

    assert len(traces) == 1
    fields = traces[0].fields
    assert fields["syntax_valid"] is False
    assert fields["mx_resolved"] is False
    assert fields["mx_hosts"] == []
    assert fields["has_gravatar"] is False
    # The evidence row anchors at a non-network sentinel so the chain
    # still has a payload hash for the dossier to render.
    assert traces[0].evidence.source_url.startswith("reckora://invalid-email/")
    assert len(traces[0].evidence.payload_sha256) == 64
    assert traces[0].evidence.raw_payload is None


# --- happy path: MX + Gravatar -------------------------------------------


async def test_collect_full_profile(httpx_mock: HTTPXMock, email_alice: Identifier) -> None:
    """Domain has MX + Gravatar serves a profile — every field populated."""
    httpx_mock.add_response(
        url=_gravatar_url("alice@example.com"),
        json={
            "entry": [
                {
                    "id": "1234",
                    "displayName": "Alice Liddell",
                    "aboutMe": "OSINT analyst.",
                    "currentLocation": "Wonderland",
                    "profileUrl": "https://gravatar.com/alice",
                }
            ]
        },
    )
    resolver = _stub(
        {
            ("example.com", "MX"): [
                _StubMxRdata("mx1.example.com."),
                _StubMxRdata("mx2.example.com."),
            ]
        }
    )
    async with httpx.AsyncClient() as client:
        collector = EmailCollector(client=client, resolver=resolver)
        traces = await collector.collect(email_alice)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.source == TraceSource.EMAIL_PROFILE
    fields = trace.fields
    assert fields["email"] == "alice@example.com"
    assert fields["local_part"] == "alice"
    assert fields["domain"] == "example.com"
    assert fields["syntax_valid"] is True
    assert fields["mx_resolved"] is True
    # Trailing dots stripped, lowercased, sorted.
    assert fields["mx_hosts"] == ["mx1.example.com", "mx2.example.com"]
    assert fields["has_gravatar"] is True
    assert fields["gravatar_url"] == "https://gravatar.com/alice"
    assert fields["gravatar_display_name"] == "Alice Liddell"
    assert fields["gravatar_about"] == "OSINT analyst."
    assert fields["gravatar_location"] == "Wonderland"


# --- MX-only edges -------------------------------------------------------


async def test_collect_no_mx_records(httpx_mock: HTTPXMock, email_alice: Identifier) -> None:
    """Domain has no MX → mx_resolved=False, mx_hosts=[]."""
    httpx_mock.add_response(url=_gravatar_url("alice@example.com"), status_code=404)
    resolver = _stub(raises=dns.resolver.NoAnswer())  # type: ignore[no-untyped-call]
    async with httpx.AsyncClient() as client:
        collector = EmailCollector(client=client, resolver=resolver)
        traces = await collector.collect(email_alice)

    assert traces[0].fields["mx_resolved"] is False
    assert traces[0].fields["mx_hosts"] == []
    assert traces[0].fields["has_gravatar"] is False


async def test_collect_mx_nxdomain_treated_as_no_records(
    httpx_mock: HTTPXMock, email_alice: Identifier
) -> None:
    """NXDOMAIN on the email domain: signal is "domain doesn't exist" but
    we still emit a clean trace so the dossier can render the finding."""
    httpx_mock.add_response(url=_gravatar_url("alice@example.com"), status_code=404)
    resolver = _stub(raises=dns.resolver.NXDOMAIN())  # type: ignore[no-untyped-call]
    async with httpx.AsyncClient() as client:
        collector = EmailCollector(client=client, resolver=resolver)
        traces = await collector.collect(email_alice)

    assert traces[0].fields["mx_resolved"] is False
    assert traces[0].fields["mx_hosts"] == []


async def test_collect_mx_timeout_treated_as_no_records(
    httpx_mock: HTTPXMock, email_alice: Identifier
) -> None:
    """A flaky resolver MUST NOT take down the collector."""
    httpx_mock.add_response(url=_gravatar_url("alice@example.com"), status_code=404)
    resolver = _stub(raises=dns.exception.Timeout())  # type: ignore[no-untyped-call]
    async with httpx.AsyncClient() as client:
        collector = EmailCollector(client=client, resolver=resolver)
        traces = await collector.collect(email_alice)

    assert traces[0].fields["mx_resolved"] is False


async def test_collect_mx_dedupes_and_sorts(httpx_mock: HTTPXMock, email_alice: Identifier) -> None:
    """Reddit ships duplicate MX entries on some domains; we de-dupe."""
    httpx_mock.add_response(url=_gravatar_url("alice@example.com"), status_code=404)
    resolver = _stub(
        {
            ("example.com", "MX"): [
                _StubMxRdata("Z.example.com."),
                _StubMxRdata("a.example.com."),
                _StubMxRdata("a.example.com."),
            ]
        }
    )
    async with httpx.AsyncClient() as client:
        collector = EmailCollector(client=client, resolver=resolver)
        traces = await collector.collect(email_alice)

    assert traces[0].fields["mx_hosts"] == ["a.example.com", "z.example.com"]


# --- Gravatar edges ------------------------------------------------------


async def test_collect_gravatar_404_no_profile(
    httpx_mock: HTTPXMock, email_alice: Identifier
) -> None:
    httpx_mock.add_response(url=_gravatar_url("alice@example.com"), status_code=404)
    resolver = _stub({("example.com", "MX"): [_StubMxRdata("mx.x.")]})
    async with httpx.AsyncClient() as client:
        collector = EmailCollector(client=client, resolver=resolver)
        traces = await collector.collect(email_alice)

    fields = traces[0].fields
    assert fields["has_gravatar"] is False
    assert fields["gravatar_url"] is None
    assert fields["gravatar_display_name"] is None
    assert fields["gravatar_about"] is None
    assert fields["gravatar_location"] is None
    # MX still surfaces correctly even without a Gravatar.
    assert fields["mx_hosts"] == ["mx.x"]


async def test_collect_gravatar_non_json_body_treated_as_no_profile(
    httpx_mock: HTTPXMock, email_alice: Identifier
) -> None:
    httpx_mock.add_response(
        url=_gravatar_url("alice@example.com"),
        content=b"<html>404</html>",
        headers={"Content-Type": "text/html"},
    )
    resolver = _stub({("example.com", "MX"): [_StubMxRdata("mx.x.")]})
    async with httpx.AsyncClient() as client:
        collector = EmailCollector(client=client, resolver=resolver)
        traces = await collector.collect(email_alice)

    assert traces[0].fields["has_gravatar"] is False


async def test_collect_gravatar_unexpected_shape_treated_as_no_profile(
    httpx_mock: HTTPXMock, email_alice: Identifier
) -> None:
    """Gravatar sometimes ships ``{"entry": []}``; treat that as no profile."""
    httpx_mock.add_response(
        url=_gravatar_url("alice@example.com"),
        json={"entry": []},
    )
    resolver = _stub({("example.com", "MX"): [_StubMxRdata("mx.x.")]})
    async with httpx.AsyncClient() as client:
        collector = EmailCollector(client=client, resolver=resolver)
        traces = await collector.collect(email_alice)

    assert traces[0].fields["has_gravatar"] is False


async def test_collect_gravatar_500_propagates(
    httpx_mock: HTTPXMock, email_alice: Identifier
) -> None:
    """Transport errors must NOT be silently swallowed."""
    httpx_mock.add_response(url=_gravatar_url("alice@example.com"), status_code=500)
    resolver = _stub({("example.com", "MX"): [_StubMxRdata("mx.x.")]})
    async with httpx.AsyncClient() as client:
        collector = EmailCollector(client=client, resolver=resolver)
        with pytest.raises(httpx.HTTPStatusError):
            await collector.collect(email_alice)


# --- canonicalisation + headers ------------------------------------------


async def test_collect_canonicalises_email_to_lowercase(
    httpx_mock: HTTPXMock,
) -> None:
    """Mixed-case input MUST hit the lowercase Gravatar URL."""
    ident = Identifier(type=IdentifierType.EMAIL, value="Alice@Example.com")
    httpx_mock.add_response(url=_gravatar_url("alice@example.com"), status_code=404)
    resolver = _stub(raises=dns.resolver.NoAnswer())  # type: ignore[no-untyped-call]
    async with httpx.AsyncClient() as client:
        collector = EmailCollector(client=client, resolver=resolver)
        traces = await collector.collect(ident)

    assert traces[0].fields["email"] == "alice@example.com"


async def test_collect_sends_required_gravatar_headers(
    httpx_mock: HTTPXMock, email_alice: Identifier
) -> None:
    httpx_mock.add_response(url=_gravatar_url("alice@example.com"), status_code=404)
    resolver = _stub(raises=dns.resolver.NoAnswer())  # type: ignore[no-untyped-call]
    async with httpx.AsyncClient() as client:
        collector = EmailCollector(client=client, resolver=resolver)
        await collector.collect(email_alice)

    request = httpx_mock.get_requests()[0]
    assert request.headers["User-Agent"] == "Reckora/0.1"
    assert request.headers["Accept"] == "application/json"


# --- evidence ---------------------------------------------------------------


async def test_collect_evidence_drops_raw_gravatar_payload(
    httpx_mock: HTTPXMock, email_alice: Identifier
) -> None:
    """Gravatar profiles can carry PII; raw payload MUST NOT be inlined."""
    payload: dict[str, Any] = {
        "entry": [
            {
                "displayName": "Alice",
                "phoneNumbers": [{"type": "personal", "value": "+1 555 0100"}],
            }
        ]
    }
    httpx_mock.add_response(url=_gravatar_url("alice@example.com"), json=payload)
    resolver = _stub({("example.com", "MX"): [_StubMxRdata("mx.x.")]})
    async with httpx.AsyncClient() as client:
        collector = EmailCollector(client=client, resolver=resolver)
        traces = await collector.collect(email_alice)

    evidence = traces[0].evidence
    assert evidence.raw_payload is None
    assert evidence.source_url == _gravatar_url("alice@example.com")
    assert len(evidence.payload_sha256) == 64
