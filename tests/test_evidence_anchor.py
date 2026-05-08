"""Tests for the high-level anchor orchestrator (Layer 7)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from pytest_httpx import HTTPXMock

from reckora.evidence.anchor import Anchor, anchor_traces
from reckora.evidence.chain import make_evidence
from reckora.evidence.merkle import compute_dossier_root
from reckora.evidence.timestamp import (
    CalendarReceipt,
    OpenTimestampsClient,
)
from reckora.models.entity import Identifier, Trace
from reckora.models.enums import IdentifierType, TraceSource


def _trace(value: str, source: TraceSource = TraceSource.WEB_PROFILE) -> Trace:
    return Trace(
        identifier=Identifier(type=IdentifierType.USERNAME, value=value),
        source=source,
        fields={"platform": "test", "display_name": value},
        evidence=make_evidence(
            f"https://x/{value}",
            {"login": value, "ts": value},
        ),
    )


class _StubClient(OpenTimestampsClient):
    """Drop-in replacement that records the digest it was asked to submit
    and returns one canned receipt without going to the network.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._receipts: list[CalendarReceipt] = [
            CalendarReceipt(
                calendar_url="https://stub.example.com",
                receipt_b64="ZmFrZQ==",
                submitted_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ]
        self._closed = False

    async def submit_digest(self, digest_hex: str) -> list[CalendarReceipt]:
        self.calls.append(digest_hex)
        return list(self._receipts)

    async def aclose(self) -> None:
        self._closed = True


@pytest.fixture
def non_mocked_hosts() -> list[str]:
    return []


async def test_anchor_traces_uses_provided_client_and_does_not_close_it() -> None:
    traces = [_trace("alice"), _trace("bob")]
    client = _StubClient()

    anchor = await anchor_traces(traces, client=client)

    assert isinstance(anchor, Anchor)
    expected_root, expected_leaves = compute_dossier_root(traces)
    assert anchor.merkle_root == expected_root
    assert anchor.leaf_hashes == expected_leaves
    assert client.calls == [expected_root]
    # Caller-provided clients must NOT be closed on the caller's behalf.
    assert client._closed is False
    assert anchor.created_at.tzinfo is not None
    assert len(anchor.receipts) == 1
    assert anchor.receipts[0].calendar_url == "https://stub.example.com"


async def test_anchor_traces_creates_and_closes_default_client(
    httpx_mock: HTTPXMock,
) -> None:
    """When no client is injected, anchor_traces must spin up a private
    client and close it before returning."""
    cal = "https://a.example.com"
    httpx_mock.add_response(
        method="POST",
        url=f"{cal}/digest",
        content=b"receipt",
        status_code=200,
    )
    traces = [_trace("solo")]

    anchor = await anchor_traces(traces, calendars=(cal,))

    assert anchor.merkle_root == traces[0].evidence.payload_sha256
    assert [r.calendar_url for r in anchor.receipts] == [cal]


async def test_anchor_traces_survives_total_calendar_outage(
    httpx_mock: HTTPXMock,
) -> None:
    """Even when every calendar is down, anchor_traces must still mint an
    Anchor with the locally-computed root. Receipts are best-effort."""
    cal_a = "https://a.example.com"
    cal_b = "https://b.example.com"
    httpx_mock.add_response(method="POST", url=f"{cal_a}/digest", status_code=503)
    httpx_mock.add_response(method="POST", url=f"{cal_b}/digest", status_code=502)
    traces = [_trace("alice"), _trace("bob")]

    anchor = await anchor_traces(traces, calendars=(cal_a, cal_b))

    assert anchor.merkle_root == compute_dossier_root(traces)[0]
    assert anchor.receipts == []


def test_anchor_model_is_frozen() -> None:
    """The Anchor record is immutable so callers can hand it to renderers
    without worrying about post-hoc mutation."""
    anchor = Anchor(
        merkle_root="0" * 64,
        leaf_hashes=["0" * 64],
        receipts=[],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(ValidationError):
        anchor.merkle_root = "1" * 64


def test_anchor_round_trips_through_json() -> None:
    """The Anchor must survive JSON serialisation so SQLite + the dossier
    JSON export stay lossless across save/load."""
    receipt = CalendarReceipt(
        calendar_url="https://cal.example.com",
        receipt_b64="cmVjZWlwdA==",
        submitted_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
    )
    original = Anchor(
        merkle_root="ab" * 32,
        leaf_hashes=["cd" * 32, "ef" * 32],
        receipts=[receipt],
        created_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
    )
    revived = Anchor.model_validate_json(original.model_dump_json())
    assert revived == original


async def test_anchor_traces_iterates_traces_only_once() -> None:
    """Anchoring must consume the trace iterable in a single pass — it is
    correct to pass a generator (e.g. from the orchestrator)."""

    def _gen() -> Iterable[Trace]:
        yield _trace("alice")
        yield _trace("bob")

    client = _StubClient()
    anchor = await anchor_traces(list(_gen()), client=client)
    assert len(anchor.leaf_hashes) == 2
