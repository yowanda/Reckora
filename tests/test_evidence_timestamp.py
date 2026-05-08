"""Tests for the OpenTimestamps Calendar HTTP client (Layer 7)."""

from __future__ import annotations

import base64
import hashlib

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.evidence.timestamp import (
    DEFAULT_CALENDARS,
    CalendarReceipt,
    OpenTimestampsClient,
)


def _digest_hex() -> str:
    return hashlib.sha256(b"reckora-merkle-root").hexdigest()


@pytest.fixture
def non_mocked_hosts() -> list[str]:
    """Force httpx_mock to fail on any host we did NOT explicitly stub."""
    # An empty list means "every host must be mocked" — perfect for asserting
    # we never accidentally hit the real OpenTimestamps fleet from CI.
    return []


def test_default_calendars_match_upstream_ots_cli() -> None:
    """Reckora must default to the same fleet `ots stamp` uses so receipts
    are interchangeable with the upstream tooling."""
    assert "https://a.pool.opentimestamps.org" in DEFAULT_CALENDARS
    assert "https://b.pool.opentimestamps.org" in DEFAULT_CALENDARS
    assert "https://alice.btc.calendar.opentimestamps.org" in DEFAULT_CALENDARS


def test_constructor_rejects_empty_calendar_list() -> None:
    with pytest.raises(ValueError, match="at least one calendar"):
        OpenTimestampsClient(calendars=())


async def test_submit_digest_hits_each_calendar_with_raw_bytes(
    httpx_mock: HTTPXMock,
) -> None:
    digest_hex = _digest_hex()
    cal_a = "https://a.example.com"
    cal_b = "https://b.example.com"
    receipt_bytes = b"\x00\xfe\xfdmock-receipt"
    httpx_mock.add_response(
        method="POST",
        url=f"{cal_a}/digest",
        content=receipt_bytes,
        status_code=200,
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{cal_b}/digest",
        content=receipt_bytes,
        status_code=200,
    )

    client = OpenTimestampsClient(calendars=(cal_a, cal_b))
    try:
        receipts = await client.submit_digest(digest_hex)
    finally:
        await client.aclose()

    assert [r.calendar_url for r in receipts] == [cal_a, cal_b]
    for receipt in receipts:
        assert isinstance(receipt, CalendarReceipt)
        assert base64.b64decode(receipt.receipt_b64) == receipt_bytes
        assert receipt.submitted_at.tzinfo is not None

    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    for r in requests:
        assert r.method == "POST"
        # The body must be raw 32 bytes — the OpenTimestamps Calendar
        # protocol does NOT take JSON or hex on the wire.
        assert r.read() == bytes.fromhex(digest_hex)
        assert r.headers["Content-Type"] == "application/vnd.opentimestamps.v1"
        assert "Reckora" in r.headers["User-Agent"]


async def test_submit_digest_skips_500_responses_silently(
    httpx_mock: HTTPXMock,
) -> None:
    """A flaky calendar must not break anchoring — the client returns
    receipts only for the calendars that responded with a 200."""
    cal_a = "https://a.example.com"
    cal_b = "https://b.example.com"
    httpx_mock.add_response(method="POST", url=f"{cal_a}/digest", content=b"ok", status_code=200)
    httpx_mock.add_response(method="POST", url=f"{cal_b}/digest", status_code=503, text="gateway")

    client = OpenTimestampsClient(calendars=(cal_a, cal_b))
    try:
        receipts = await client.submit_digest(_digest_hex())
    finally:
        await client.aclose()

    assert [r.calendar_url for r in receipts] == [cal_a]


async def test_submit_digest_skips_empty_200_body(
    httpx_mock: HTTPXMock,
) -> None:
    cal = "https://a.example.com"
    httpx_mock.add_response(method="POST", url=f"{cal}/digest", content=b"", status_code=200)
    client = OpenTimestampsClient(calendars=(cal,))
    try:
        receipts = await client.submit_digest(_digest_hex())
    finally:
        await client.aclose()
    assert receipts == []


async def test_submit_digest_swallows_network_errors(
    httpx_mock: HTTPXMock,
) -> None:
    cal_a = "https://a.example.com"
    cal_b = "https://b.example.com"
    httpx_mock.add_exception(httpx.ConnectError("offline"), url=f"{cal_a}/digest")
    httpx_mock.add_response(method="POST", url=f"{cal_b}/digest", content=b"ok", status_code=200)

    client = OpenTimestampsClient(calendars=(cal_a, cal_b))
    try:
        receipts = await client.submit_digest(_digest_hex())
    finally:
        await client.aclose()

    assert [r.calendar_url for r in receipts] == [cal_b]


async def test_submit_digest_rejects_malformed_hex() -> None:
    client = OpenTimestampsClient(calendars=("https://a.example.com",))
    try:
        with pytest.raises(ValueError, match="must be 64 hex chars"):
            await client.submit_digest("deadbeef")
        with pytest.raises(ValueError, match="not valid hex"):
            await client.submit_digest("z" * 64)
    finally:
        await client.aclose()


async def test_caller_provided_client_is_not_closed_by_aclose(
    httpx_mock: HTTPXMock,
) -> None:
    """If the caller injects an httpx.AsyncClient (e.g. to share a pool),
    the OpenTimestampsClient must NOT close it on its own teardown."""
    httpx_mock.add_response(
        method="POST",
        url="https://a.example.com/digest",
        content=b"ok",
        status_code=200,
    )
    shared = httpx.AsyncClient()
    try:
        client = OpenTimestampsClient(
            calendars=("https://a.example.com",),
            client=shared,
        )
        await client.submit_digest(_digest_hex())
        await client.aclose()
        # Shared client is still usable after the OTS client's aclose().
        assert not shared.is_closed
    finally:
        await shared.aclose()


async def test_calendars_property_returns_configured_tuple() -> None:
    cals = ("https://x.example.com", "https://y.example.com")
    client = OpenTimestampsClient(calendars=cals)
    try:
        assert client.calendars == cals
    finally:
        await client.aclose()
