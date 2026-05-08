"""Tests for the Gravatar collector."""

from __future__ import annotations

import hashlib

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.gravatar import (
    GRAVATAR_API_BASE,
    GravatarCollector,
    _hash_email,
)
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource

EMAIL = "Person@Example.COM"
EMAIL_HASH = hashlib.md5(EMAIL.strip().lower().encode("utf-8")).hexdigest()


@pytest.fixture
def email_identifier() -> Identifier:
    return Identifier(type=IdentifierType.EMAIL, value=EMAIL)


def test_hash_email_normalises_case_and_whitespace() -> None:
    # Gravatar's canonical hash is taken on the trimmed-lowercased email
    # so two callers with the same address but different casing must
    # land on the same hash.
    expected = hashlib.md5(b"person@example.com").hexdigest()
    assert _hash_email("Person@Example.COM") == expected
    assert _hash_email("  person@example.com  ") == expected
    assert _hash_email("PERSON@EXAMPLE.COM") == expected


async def test_collect_404_returns_empty(
    httpx_mock: HTTPXMock, email_identifier: Identifier
) -> None:
    httpx_mock.add_response(
        url=f"{GRAVATAR_API_BASE}/{EMAIL_HASH}.json",
        status_code=404,
        text='"User not found"',
    )
    async with httpx.AsyncClient() as client:
        collector = GravatarCollector(client=client)
        traces = await collector.collect(email_identifier)
    assert traces == []


async def test_collect_user_not_found_string_returns_empty(
    httpx_mock: HTTPXMock, email_identifier: Identifier
) -> None:
    # Gravatar sometimes serves the literal JSON string ``"User not found"``
    # with a 200 status code (CDN-cached responses, in particular). Treat
    # that the same way we treat a 404 so the orchestrator's miss-vs-error
    # semantics stay consistent across collectors.
    httpx_mock.add_response(
        url=f"{GRAVATAR_API_BASE}/{EMAIL_HASH}.json",
        text='"User not found"',
        headers={"Content-Type": "application/json"},
    )
    async with httpx.AsyncClient() as client:
        collector = GravatarCollector(client=client)
        traces = await collector.collect(email_identifier)
    assert traces == []


async def test_collect_normalises_response(
    httpx_mock: HTTPXMock, email_identifier: Identifier
) -> None:
    httpx_mock.add_response(
        url=f"{GRAVATAR_API_BASE}/{EMAIL_HASH}.json",
        json={
            "entry": [
                {
                    "hash": EMAIL_HASH,
                    "profileUrl": f"https://gravatar.com/{EMAIL_HASH}",
                    "preferredUsername": "person",
                    "displayName": "Real Person",
                    "currentLocation": "Berlin, DE",
                    "aboutMe": "I write OSINT collectors.",
                    "thumbnailUrl": (f"https://2.gravatar.com/avatar/{EMAIL_HASH}"),
                    "photos": [
                        {
                            "value": (f"https://2.gravatar.com/avatar/{EMAIL_HASH}"),
                            "type": "thumbnail",
                        }
                    ],
                    "accounts": [
                        {
                            "domain": "twitter.com",
                            "shortname": "twitter",
                            "username": "person",
                            "verified": "true",
                            "url": "https://twitter.com/person",
                        },
                        {
                            "domain": "github.com",
                            "shortname": "github",
                            "username": "person",
                            "verified": "true",
                            "url": "https://github.com/person",
                        },
                    ],
                }
            ]
        },
    )
    async with httpx.AsyncClient() as client:
        collector = GravatarCollector(client=client)
        traces = await collector.collect(email_identifier)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.source == TraceSource.GRAVATAR_API
    assert trace.fields["platform"] == "gravatar"
    # The collector exposes the *hash* — not the email itself — so a
    # downstream pipeline never has to handle the plaintext PII.
    assert trace.fields["email_hash"] == EMAIL_HASH
    assert "email" not in trace.fields
    assert trace.fields["profile_url"] == f"https://gravatar.com/{EMAIL_HASH}"
    assert trace.fields["preferred_username"] == "person"
    assert trace.fields["display_name"] == "Real Person"
    assert trace.fields["bio"] == "I write OSINT collectors."
    assert trace.fields["location"] == "Berlin, DE"
    assert trace.fields["profile_photo_url"] == (f"https://2.gravatar.com/avatar/{EMAIL_HASH}")
    assert trace.fields["accounts"] == [
        {"platform": "twitter", "username": "person", "url": "https://twitter.com/person"},
        {"platform": "github", "username": "person", "url": "https://github.com/person"},
    ]
    assert trace.fields["is_active"] is True
    assert trace.evidence.source_url == f"{GRAVATAR_API_BASE}/{EMAIL_HASH}.json"
    assert len(trace.evidence.payload_sha256) == 64
    # ``keep_raw=False`` keeps Gravatar profile JSON out of the dossier
    # because the array of linked accounts and the embedded URLs can be
    # very long for power users.
    assert trace.evidence.raw_payload is None


async def test_collect_falls_back_to_photos_when_thumbnail_missing(
    httpx_mock: HTTPXMock, email_identifier: Identifier
) -> None:
    # Older Gravatar accounts only populate ``photos[]`` (no
    # ``thumbnailUrl``). The collector should still surface a usable
    # avatar URL so the perceptual-hash collector can pivot from it.
    httpx_mock.add_response(
        url=f"{GRAVATAR_API_BASE}/{EMAIL_HASH}.json",
        json={
            "entry": [
                {
                    "hash": EMAIL_HASH,
                    "profileUrl": f"https://gravatar.com/{EMAIL_HASH}",
                    "displayName": "Legacy User",
                    "photos": [
                        {
                            "value": "https://gravatar.com/avatar/legacy.png",
                            "type": "thumbnail",
                        }
                    ],
                }
            ]
        },
    )
    async with httpx.AsyncClient() as client:
        collector = GravatarCollector(client=client)
        traces = await collector.collect(email_identifier)
    assert len(traces) == 1
    assert traces[0].fields["profile_photo_url"] == "https://gravatar.com/avatar/legacy.png"


async def test_collect_minimal_account_marked_active_when_displayed(
    httpx_mock: HTTPXMock, email_identifier: Identifier
) -> None:
    # An account with just a display name and no links / bio still
    # surfaces as active because ``displayName`` is itself a public
    # identity claim.
    httpx_mock.add_response(
        url=f"{GRAVATAR_API_BASE}/{EMAIL_HASH}.json",
        json={
            "entry": [
                {
                    "hash": EMAIL_HASH,
                    "displayName": "Just A Name",
                }
            ]
        },
    )
    async with httpx.AsyncClient() as client:
        collector = GravatarCollector(client=client)
        traces = await collector.collect(email_identifier)
    assert len(traces) == 1
    fields = traces[0].fields
    assert fields["display_name"] == "Just A Name"
    assert fields["preferred_username"] is None
    assert fields["bio"] is None
    assert fields["accounts"] == []
    assert fields["is_active"] is True
    # No ``profileUrl`` in the response — fall back to the canonical
    # ``/{hash}`` form so downstream consumers always get a URL.
    assert fields["profile_url"] == f"{GRAVATAR_API_BASE}/{EMAIL_HASH}"


async def test_collect_empty_account_marked_inactive(
    httpx_mock: HTTPXMock, email_identifier: Identifier
) -> None:
    # An account that has registered but never filled out any profile
    # data still surfaces as a trace so the absence is itself a finding,
    # but ``is_active`` is False.
    httpx_mock.add_response(
        url=f"{GRAVATAR_API_BASE}/{EMAIL_HASH}.json",
        json={
            "entry": [
                {
                    "hash": EMAIL_HASH,
                    "profileUrl": f"https://gravatar.com/{EMAIL_HASH}",
                }
            ]
        },
    )
    async with httpx.AsyncClient() as client:
        collector = GravatarCollector(client=client)
        traces = await collector.collect(email_identifier)
    assert len(traces) == 1
    fields = traces[0].fields
    assert fields["is_active"] is False
    assert fields["accounts"] == []


async def test_collect_drops_partial_account_entries(
    httpx_mock: HTTPXMock, email_identifier: Identifier
) -> None:
    # Linked-account entries missing any of ``shortname`` / ``username``
    # / ``url`` are noise; ignoring them avoids feeding the correlation
    # engine half-formed pivots.
    httpx_mock.add_response(
        url=f"{GRAVATAR_API_BASE}/{EMAIL_HASH}.json",
        json={
            "entry": [
                {
                    "hash": EMAIL_HASH,
                    "displayName": "Partial",
                    "accounts": [
                        {"shortname": "twitter", "username": "valid", "url": "https://x"},
                        {"shortname": "github", "username": "missing-url"},
                        {"shortname": "linkedin", "url": "https://l"},
                        {"username": "noplatform", "url": "https://n"},
                    ],
                }
            ]
        },
    )
    async with httpx.AsyncClient() as client:
        collector = GravatarCollector(client=client)
        traces = await collector.collect(email_identifier)
    assert len(traces) == 1
    assert traces[0].fields["accounts"] == [
        {"platform": "twitter", "username": "valid", "url": "https://x"},
    ]


async def test_collect_skips_unsupported_identifier() -> None:
    collector = GravatarCollector()
    ident = Identifier(type=IdentifierType.USERNAME, value="alice")
    traces = await collector.collect(ident)
    assert traces == []


@pytest.mark.parametrize(
    "value",
    [
        # Empty string after trimming.
        "   ",
        # Missing ``@`` — not a valid email shape.
        "not-an-email",
        # Bare local-part with no domain.
        "alice@",
        "alice",
    ],
)
async def test_collect_rejects_non_email_shapes(value: str) -> None:
    # Pre-filtering keeps the orchestrator from spending a request on
    # obvious non-email strings if a caller mis-typed an identifier.
    collector = GravatarCollector()
    traces = await collector.collect(Identifier(type=IdentifierType.EMAIL, value=value))
    # The first ``not-an-email`` and ``alice`` cases legitimately contain
    # no ``@`` and fail the shape gate; ``alice@`` and ``"   "`` likewise
    # short-circuit. We assert no network call was made by checking the
    # collector returned an empty list — the httpx_mock fixture would
    # have raised on an unmatched call.
    assert traces == []


async def test_collect_empty_entry_array_returns_empty(
    httpx_mock: HTTPXMock, email_identifier: Identifier
) -> None:
    # Some Gravatar deployments return ``{"entry": []}`` for
    # not-yet-registered accounts. Treat empty entry arrays as a miss.
    httpx_mock.add_response(
        url=f"{GRAVATAR_API_BASE}/{EMAIL_HASH}.json",
        json={"entry": []},
    )
    async with httpx.AsyncClient() as client:
        collector = GravatarCollector(client=client)
        traces = await collector.collect(email_identifier)
    assert traces == []
