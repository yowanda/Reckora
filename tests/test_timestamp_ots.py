"""Tests for the OpenTimestamps stamp + verify wrappers.

The real OpenTimestamps calendar network involves an HTTP fan-out
plus a Bitcoin-block wait that can run for hours. Both are
inappropriate for a unit-test suite, so we use a small stub calendar
that returns a deterministic :class:`Timestamp` chain — the result
exercises the same merge/serialise/deserialise code paths as a real
calendar, but the test stays fully offline and runs in milliseconds.
"""

from __future__ import annotations

import hashlib
from typing import cast

import pytest
from opentimestamps.core.notary import (  # type: ignore[import-untyped]
    BitcoinBlockHeaderAttestation,
    PendingAttestation,
)
from opentimestamps.core.op import OpAppend  # type: ignore[import-untyped]
from opentimestamps.core.timestamp import Timestamp  # type: ignore[import-untyped]

from reckora.timestamp import ots
from reckora.timestamp.ots import (
    AttestationStatus,
    StampError,
    stamp_root,
    verify_receipt,
)


def _sha(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


# ---------------------------------------------------------------------------
# Stub calendar — replaces ``RemoteCalendar`` for tests.
# ---------------------------------------------------------------------------


class _StubCalendar:
    """Drop-in replacement for ``RemoteCalendar`` that does no network I/O.

    Each instance crafts a :class:`Timestamp` chain that *commits to
    the submitted root* via a one-byte-suffix operation followed by an
    attestation. The exact suffix is derived from the calendar URL so
    different calendars produce distinct chains, mimicking the real
    server set.
    """

    def __init__(
        self,
        url: str,
        *,
        attestation: object | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.url = url
        self._attestation = attestation or PendingAttestation(url)
        self._raises = raises

    def submit(self, digest: bytes) -> Timestamp:
        if self._raises is not None:
            raise self._raises
        ts = Timestamp(digest)
        # A unique per-calendar suffix so each chain is distinct.
        suffix = hashlib.sha256(self.url.encode()).digest()[:3]
        sub_ts = ts.ops.add(OpAppend(suffix))
        sub_ts.attestations.add(self._attestation)
        return ts


def _patch_calendars(
    monkeypatch: pytest.MonkeyPatch,
    *,
    builders: dict[str, _StubCalendar] | None = None,
    default_attestation: object | None = None,
    default_raises: Exception | None = None,
) -> dict[str, _StubCalendar]:
    """Swap ``ots.RemoteCalendar`` for a stub factory keyed by URL.

    ``builders`` lets a test pin a specific stub per URL; URLs not in
    the mapping fall back to the defaults. The returned dict captures
    every calendar instantiated so assertions can inspect what got
    submitted.
    """
    instances: dict[str, _StubCalendar] = {}

    def factory(url: str, *, user_agent: str = "Reckora/0.1") -> object:
        del user_agent  # unused in tests
        if builders is not None and url in builders:
            stub = builders[url]
        else:
            stub = _StubCalendar(
                url,
                attestation=default_attestation,
                raises=default_raises,
            )
        instances[url] = stub
        return stub

    monkeypatch.setattr(ots, "RemoteCalendar", factory)
    return instances


# ---------------------------------------------------------------------------
# stamp_root
# ---------------------------------------------------------------------------


def test_stamp_root_roundtrips_through_serialize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_calendars(monkeypatch)
    root = _sha(b"some-merkle-root")
    receipt = stamp_root(root, calendars=["https://stub-1.example/calendar"])

    result = verify_receipt(receipt, root)
    assert result.valid
    assert result.receipt_root_sha256 == root.hex()
    assert result.expected_root_sha256 == root.hex()
    # PendingAttestation -> status is PENDING by default.
    assert result.status is AttestationStatus.PENDING
    assert result.pending_calendars == ("https://stub-1.example/calendar",)


def test_stamp_root_rejects_non_32_byte_root(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_calendars(monkeypatch)
    with pytest.raises(StampError):
        stamp_root(b"\x00" * 31)


def test_stamp_root_requires_at_least_one_calendar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_calendars(monkeypatch)
    with pytest.raises(StampError):
        stamp_root(_sha(b"r"), calendars=[])


def test_stamp_root_tolerates_partial_calendar_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builders = {
        "https://stub-bad.example/calendar": _StubCalendar(
            "https://stub-bad.example/calendar",
            raises=RuntimeError("boom"),
        ),
        "https://stub-good.example/calendar": _StubCalendar(
            "https://stub-good.example/calendar",
        ),
    }
    _patch_calendars(monkeypatch, builders=builders)
    root = _sha(b"resilience")
    receipt = stamp_root(
        root,
        calendars=[
            "https://stub-bad.example/calendar",
            "https://stub-good.example/calendar",
        ],
    )
    result = verify_receipt(receipt, root)
    assert result.valid
    assert result.pending_calendars == ("https://stub-good.example/calendar",)


def test_stamp_root_raises_when_every_calendar_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_calendars(monkeypatch, default_raises=RuntimeError("rate-limited"))
    with pytest.raises(StampError) as exc:
        stamp_root(
            _sha(b"r"),
            calendars=["https://stub-1.example/c", "https://stub-2.example/c"],
        )
    assert "rate-limited" in str(exc.value)


def test_stamp_root_merges_multiple_calendar_attestations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_calendars(monkeypatch)
    root = _sha(b"multi")
    receipt = stamp_root(
        root,
        calendars=[
            "https://stub-a.example/calendar",
            "https://stub-b.example/calendar",
        ],
    )
    result = verify_receipt(receipt, root)
    assert result.valid
    assert set(result.pending_calendars) == {
        "https://stub-a.example/calendar",
        "https://stub-b.example/calendar",
    }


# ---------------------------------------------------------------------------
# verify_receipt — strongest-attestation reporting
# ---------------------------------------------------------------------------


def test_verify_receipt_reports_bitcoin_when_block_attestation_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bitcoin_attestation = BitcoinBlockHeaderAttestation(height=820000)
    _patch_calendars(monkeypatch, default_attestation=bitcoin_attestation)
    root = _sha(b"anchored")
    receipt = stamp_root(root, calendars=["https://stub.example/c"])

    result = verify_receipt(receipt, root)
    assert result.status is AttestationStatus.BITCOIN
    assert result.bitcoin_block_height == 820000
    assert result.litecoin_block_height is None


def test_verify_receipt_picks_lowest_bitcoin_height_when_multiple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builders = {
        "https://stub-old.example/c": _StubCalendar(
            "https://stub-old.example/c",
            attestation=BitcoinBlockHeaderAttestation(height=700000),
        ),
        "https://stub-new.example/c": _StubCalendar(
            "https://stub-new.example/c",
            attestation=BitcoinBlockHeaderAttestation(height=820000),
        ),
    }
    _patch_calendars(monkeypatch, builders=builders)
    root = _sha(b"oldest")
    receipt = stamp_root(
        root,
        calendars=[
            "https://stub-old.example/c",
            "https://stub-new.example/c",
        ],
    )
    result = verify_receipt(receipt, root)
    # Earliest block wins — that's the canonical "this existed by"
    # answer the receipt is meant to give.
    assert result.bitcoin_block_height == 700000


def test_verify_receipt_flags_root_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_calendars(monkeypatch)
    receipt = stamp_root(_sha(b"committed"), calendars=["https://stub.example/c"])

    other_root = _sha(b"different")
    result = verify_receipt(receipt, other_root)
    assert not result.valid
    assert result.expected_root_sha256 == other_root.hex()
    assert result.receipt_root_sha256 == _sha(b"committed").hex()


def test_verify_receipt_rejects_corrupt_bytes() -> None:
    with pytest.raises(StampError):
        verify_receipt(b"\x00\x01\x02not-a-receipt", _sha(b"r"))


def test_verify_receipt_rejects_bad_expected_root_length() -> None:
    # Defensive: catch caller bugs that pass a shortened hash.
    with pytest.raises(StampError):
        verify_receipt(b"", _sha(b"r")[:31])


def test_verify_receipt_returns_none_status_for_unknown_attestations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A calendar that returns a chain whose only attestation is an
    # ``UnknownAttestation`` (a future / experimental type the
    # current code-base doesn't classify) should still parse and
    # report status=NONE — we surface the "nothing recognised" case
    # explicitly so callers spot it instead of a misleading "valid".
    from opentimestamps.core.notary import (  # type: ignore[import-untyped]
        UnknownAttestation,
    )

    class _UnknownAttestationCalendar:
        def __init__(self, url: str) -> None:
            self.url = url

        def submit(self, digest: bytes) -> Timestamp:
            ts = Timestamp(digest)
            sub = ts.ops.add(OpAppend(b"\x01\x02\x03"))
            sub.attestations.add(UnknownAttestation(b"\x99" * 8, b"opaque"))
            return ts

    def factory(url: str, *, user_agent: str = "Reckora/0.1") -> object:
        del user_agent
        return _UnknownAttestationCalendar(url)

    monkeypatch.setattr(ots, "RemoteCalendar", factory)
    root = _sha(b"empty-attest")
    receipt = stamp_root(root, calendars=["https://stub.example/c"])
    result = verify_receipt(receipt, root)
    assert result.status is AttestationStatus.NONE
    assert result.valid


# ---------------------------------------------------------------------------
# pending-calendars list shape
# ---------------------------------------------------------------------------


def test_verify_receipt_pending_calendars_are_sorted_and_deduped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builders = {
        "https://stub-z.example/c": _StubCalendar("https://stub-z.example/c"),
        "https://stub-a.example/c": _StubCalendar("https://stub-a.example/c"),
    }
    _patch_calendars(monkeypatch, builders=builders)
    root = _sha(b"sorted")
    receipt = stamp_root(
        root,
        calendars=[
            "https://stub-z.example/c",
            "https://stub-a.example/c",
        ],
    )
    result = verify_receipt(receipt, root)
    # Tuple comparison matters here — we promise ordering.
    assert result.pending_calendars == (
        "https://stub-a.example/c",
        "https://stub-z.example/c",
    )


def test_stamp_root_propagates_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    class _RecordingCalendar(_StubCalendar):
        def __init__(self, url: str, *, user_agent: str = "Reckora/0.1") -> None:
            seen.append(user_agent)
            super().__init__(url)

    def factory(url: str, *, user_agent: str = "Reckora/0.1") -> object:
        return _RecordingCalendar(url, user_agent=user_agent)

    monkeypatch.setattr(ots, "RemoteCalendar", factory)
    stamp_root(
        _sha(b"ua"),
        calendars=["https://stub.example/c"],
        user_agent="reckora-tests/0.0",
    )
    assert seen == ["reckora-tests/0.0"]


# ---------------------------------------------------------------------------
# Type-narrowing helpers used by the stub
# ---------------------------------------------------------------------------


def test_stub_calendar_matches_remote_calendar_shape() -> None:
    """Quick smoke-test that the stub is shape-compatible with the real type.

    We don't import ``RemoteCalendar`` at runtime here — that would
    make the test file pull in the real package init twice — but we
    do require the stub to expose ``submit(digest)`` with the same
    return-type contract.
    """
    stub = _StubCalendar("https://stub.example/c")
    ts = stub.submit(_sha(b"x"))
    # ``cast`` keeps mypy happy without forcing the stub to inherit
    # from the real class.
    assert isinstance(cast(Timestamp, ts), Timestamp)
