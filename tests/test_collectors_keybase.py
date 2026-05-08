"""Tests for the Keybase collector."""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.keybase import (
    KEYBASE_API_BASE,
    KeybaseCollector,
)
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource


@pytest.fixture
def chris_identifier() -> Identifier:
    return Identifier(type=IdentifierType.USERNAME, value="chrisco")


def _lookup_url(username: str) -> str:
    return f"{KEYBASE_API_BASE}/user/lookup.json?usernames={username}"


async def test_collect_user_not_found_returns_empty(
    httpx_mock: HTTPXMock, chris_identifier: Identifier
) -> None:
    # Keybase signals "no such user" by leaving ``them[0]`` as a literal
    # ``null`` inside an otherwise OK envelope (status.code == 0). Treat
    # that exactly the same as a 404 from the GitHub or HN collectors so
    # the orchestrator's miss-vs-error semantics stay consistent.
    httpx_mock.add_response(
        url=_lookup_url("chrisco"),
        json={"status": {"code": 0, "name": "OK"}, "them": [None]},
    )
    async with httpx.AsyncClient() as client:
        collector = KeybaseCollector(client=client)
        traces = await collector.collect(chris_identifier)
    assert traces == []


async def test_collect_input_error_returns_empty(httpx_mock: HTTPXMock) -> None:
    # The lookup endpoint reports malformed input through ``status.code``
    # (100 = INPUT_ERROR), not via a 4xx HTTP response. The collector
    # should mute these as a miss rather than letting them bubble up.
    httpx_mock.add_response(
        url=_lookup_url("foo_bar"),
        json={"status": {"code": 100, "name": "INPUT_ERROR"}},
    )
    async with httpx.AsyncClient() as client:
        collector = KeybaseCollector(client=client)
        traces = await collector.collect(Identifier(type=IdentifierType.USERNAME, value="foo_bar"))
    assert traces == []


async def test_collect_normalises_response(
    httpx_mock: HTTPXMock, chris_identifier: Identifier
) -> None:
    httpx_mock.add_response(
        url=_lookup_url("chrisco"),
        json={
            "status": {"code": 0, "name": "OK"},
            "them": [
                {
                    "basics": {
                        "username": "chrisco",
                        "ctime": 1414200527,
                        "status": 0,
                    },
                    "profile": {
                        "full_name": "Chris Coyne",
                        "bio": "Co-founder.",
                        "location": "Brooklyn, NY",
                    },
                    "proofs_summary": {
                        "all": [
                            {
                                "proof_type": "twitter",
                                "nametag": "chrisco",
                                "service_url": "https://twitter.com/chrisco",
                                "state": 1,
                            },
                            {
                                "proof_type": "github",
                                "nametag": "chrisco-stale",
                                "service_url": "https://github.com/chrisco-stale",
                                "state": 2,  # revoked, must be filtered out
                            },
                            {
                                "proof_type": "reddit",
                                "nametag": "chrisco",
                                "service_url": "https://reddit.com/user/chrisco",
                                "state": 1,
                            },
                        ],
                    },
                    "public_keys": {
                        "primary": {
                            "key_fingerprint": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                            "kid": "0101...",
                        }
                    },
                }
            ],
        },
    )
    async with httpx.AsyncClient() as client:
        collector = KeybaseCollector(client=client)
        traces = await collector.collect(chris_identifier)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.source == TraceSource.KEYBASE_API
    assert trace.fields["platform"] == "keybase"
    assert trace.fields["username"] == "chrisco"
    assert trace.fields["profile_url"] == "https://keybase.io/chrisco"
    assert trace.fields["display_name"] == "Chris Coyne"
    assert trace.fields["bio"] == "Co-founder."
    assert trace.fields["location"] == "Brooklyn, NY"
    assert trace.fields["created_at"] == "2014-10-25T01:28:47+00:00"
    assert trace.fields["has_pgp_key"] is True
    assert trace.fields["pgp_fingerprint"] == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    assert trace.fields["is_active"] is True
    # Only live proofs (state == 1) are surfaced; the revoked GitHub
    # proof must be dropped entirely so it never feeds correlation.
    proofs = trace.fields["proofs"]
    assert len(proofs) == 2
    platforms = sorted(p["platform"] for p in proofs)
    assert platforms == ["reddit", "twitter"]
    assert all("identity" in p and "url" in p for p in proofs)
    assert trace.evidence.source_url == _lookup_url("chrisco")
    assert len(trace.evidence.payload_sha256) == 64
    assert trace.evidence.raw_payload is None  # Keybase responses are dropped


async def test_collect_empty_account_marked_inactive(httpx_mock: HTTPXMock) -> None:
    # An account that registered but never posted a proof, never set a
    # PGP key, and left their profile blank is still surfaced as a
    # trace so the absence of activity is itself an intelligence
    # finding — but ``is_active`` is False.
    httpx_mock.add_response(
        url=_lookup_url("ghost"),
        json={
            "status": {"code": 0, "name": "OK"},
            "them": [
                {
                    "basics": {"username": "ghost", "ctime": 1700000000},
                    "profile": {},
                    "proofs_summary": {"all": []},
                    "public_keys": {},
                }
            ],
        },
    )
    async with httpx.AsyncClient() as client:
        collector = KeybaseCollector(client=client)
        traces = await collector.collect(Identifier(type=IdentifierType.USERNAME, value="ghost"))

    assert len(traces) == 1
    trace = traces[0]
    assert trace.fields["is_active"] is False
    assert trace.fields["bio"] is None
    assert trace.fields["display_name"] is None
    assert trace.fields["proofs"] == []
    assert trace.fields["has_pgp_key"] is False
    assert trace.fields["pgp_fingerprint"] is None


async def test_collect_skips_unsupported_identifier() -> None:
    collector = KeybaseCollector()
    ident = Identifier(type=IdentifierType.DOMAIN, value="example.com")
    traces = await collector.collect(ident)
    assert traces == []


@pytest.mark.parametrize(
    "value",
    [
        # Too long for Keybase (max 16 chars).
        "thisusernameiswaytoolongforkeybase",
        # Single character — Keybase requires at least 2.
        "a",
        # Hyphen is valid for HN but not for Keybase.
        "with-dash",
        # Wallet / hex strings that ride on USERNAME from upstream
        # callers should never produce a network request.
        "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
        # Email shape.
        "alice@example.com",
    ],
)
async def test_collect_rejects_non_keybase_username_shapes(value: str) -> None:
    # Pre-filtering keeps the orchestrator from spending a request on
    # obvious non-Keybase strings (Bitcoin addresses, emails, hyphenated
    # handles, ...). The collector must not produce a network request
    # for any of these — the test deliberately does not register a
    # ``httpx_mock`` response, which means an accidental request would
    # raise.
    collector = KeybaseCollector()
    traces = await collector.collect(Identifier(type=IdentifierType.USERNAME, value=value))
    assert traces == []


async def test_collect_lowercases_username_before_request(httpx_mock: HTTPXMock) -> None:
    # Keybase normalises every username to lowercase server-side, but
    # the ``usernames=`` query parameter is *case-sensitive* in the
    # validation step. Lowercase before the round-trip so casing drift
    # in upstream callers (e.g. ``ChrisCo``) doesn't turn into a
    # silent INPUT_ERROR miss.
    httpx_mock.add_response(
        url=_lookup_url("chrisco"),
        json={
            "status": {"code": 0, "name": "OK"},
            "them": [
                {
                    "basics": {"username": "chrisco", "ctime": 1414200527},
                    "profile": {"full_name": "Chris Coyne"},
                    "proofs_summary": {"all": []},
                    "public_keys": {},
                }
            ],
        },
    )
    async with httpx.AsyncClient() as client:
        collector = KeybaseCollector(client=client)
        traces = await collector.collect(Identifier(type=IdentifierType.USERNAME, value="ChrisCo"))
    assert len(traces) == 1
    # The trace reflects the server-canonical (lowercase) username so
    # downstream identifier joins stay consistent regardless of the
    # casing the seed identifier was minted with.
    assert traces[0].fields["username"] == "chrisco"
    assert traces[0].fields["profile_url"] == "https://keybase.io/chrisco"


async def test_collect_proof_without_url_is_kept_with_empty_string(
    httpx_mock: HTTPXMock,
) -> None:
    # Keybase occasionally returns a live proof without a ``service_url``
    # (e.g. legacy proofs whose service has been retired). Surface the
    # proof anyway with an empty string for the URL — losing the proof
    # entirely would silently drop a verified identity link.
    httpx_mock.add_response(
        url=_lookup_url("chrisco"),
        json={
            "status": {"code": 0, "name": "OK"},
            "them": [
                {
                    "basics": {"username": "chrisco", "ctime": 1414200527},
                    "profile": {},
                    "proofs_summary": {
                        "all": [
                            {
                                "proof_type": "dns",
                                "nametag": "example.com",
                                "state": 1,
                            }
                        ]
                    },
                    "public_keys": {},
                }
            ],
        },
    )
    async with httpx.AsyncClient() as client:
        collector = KeybaseCollector(client=client)
        traces = await collector.collect(Identifier(type=IdentifierType.USERNAME, value="chrisco"))
    assert len(traces) == 1
    proofs = traces[0].fields["proofs"]
    assert proofs == [{"platform": "dns", "identity": "example.com", "url": ""}]
