"""Tests for the dossier-aware Merkle + OTS wrappers.

The receipt module is the seam between Reckora's domain model
(``SavedDossier`` / ``Subject`` / ``Trace`` / ``Evidence``) and the
pure Merkle + OTS primitives. These tests pin the contract so a
later refactor can swap collectors / persistence without
invalidating receipts that are already on disk.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from reckora.models.entity import Evidence, Identifier, Subject, Trace
from reckora.models.enums import IdentifierType, TraceSource
from reckora.persistence.repository import SavedDossier
from reckora.timestamp import ots
from reckora.timestamp.receipt import (
    DossierTimestamp,
    compute_dossier_root,
    stamp_dossier,
    verify_dossier,
)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_dossier(
    *,
    subject_id: str = "subj-12345",
    payload_hashes: list[str] | None = None,
) -> SavedDossier:
    """Construct a minimal :class:`SavedDossier` for tests.

    ``payload_hashes`` populates one trace per hash with deterministic
    metadata.
    """
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    traces: list[Trace] = []
    for h in payload_hashes or []:
        traces.append(
            Trace(
                source=TraceSource.WEB_PROFILE,
                identifier=seed,
                fields={"username": "alice"},
                evidence=Evidence(
                    source_url=f"https://example.test/{h[:8]}",
                    payload_sha256=h,
                    fetched_at=datetime(2025, 1, 1, tzinfo=UTC),
                ),
            )
        )
    subject = Subject(id=subject_id, seed_identifier=seed, traces=traces)
    return SavedDossier(
        id=subject_id,
        subject=subject,
        traces=traces,
        edges=[],
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# compute_dossier_root
# ---------------------------------------------------------------------------


def test_compute_dossier_root_returns_sorted_leaves() -> None:
    hashes = [_hash(f"e-{i}".encode()) for i in range(3)]
    dossier = _build_dossier(payload_hashes=hashes)
    root, leaf_hashes = compute_dossier_root(dossier)
    assert leaf_hashes == sorted(hashes)
    assert len(root) == 32


def test_compute_dossier_root_for_empty_dossier_returns_sentinel() -> None:
    dossier = _build_dossier(payload_hashes=[])
    root, leaves = compute_dossier_root(dossier)
    assert root == b"\x00" * 32
    assert leaves == []


def test_compute_dossier_root_is_independent_of_trace_order() -> None:
    hashes = [_hash(f"e-{i}".encode()) for i in range(5)]
    a = _build_dossier(payload_hashes=hashes)
    b = _build_dossier(payload_hashes=list(reversed(hashes)))
    assert compute_dossier_root(a)[0] == compute_dossier_root(b)[0]


# ---------------------------------------------------------------------------
# stamp_dossier (uses the same stub-calendar pattern as test_timestamp_ots)
# ---------------------------------------------------------------------------


class _StubCalendar:
    def __init__(self, url: str) -> None:
        self.url = url

    def submit(self, digest: bytes) -> object:
        from opentimestamps.core.notary import (  # type: ignore[import-untyped]
            PendingAttestation,
        )
        from opentimestamps.core.op import OpAppend  # type: ignore[import-untyped]
        from opentimestamps.core.timestamp import (  # type: ignore[import-untyped]
            Timestamp,
        )

        ts = Timestamp(digest)
        sub = ts.ops.add(OpAppend(b"\xaa\xbb"))
        sub.attestations.add(PendingAttestation(self.url))
        return ts


def _patch_calendars(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(url: str, *, user_agent: str = "Reckora/0.1") -> object:
        del user_agent
        return _StubCalendar(url)

    monkeypatch.setattr(ots, "RemoteCalendar", factory)


def test_stamp_dossier_records_leaf_hashes_at_stamp_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_calendars(monkeypatch)
    hashes = [_hash(f"e-{i}".encode()) for i in range(3)]
    dossier = _build_dossier(payload_hashes=hashes)

    stamp = stamp_dossier(dossier, calendars=["https://stub.example/c"])
    assert stamp.subject_id == dossier.subject.id
    assert stamp.merkle_root_sha256 == compute_dossier_root(dossier)[0].hex()
    assert stamp.leaf_hashes == tuple(sorted(hashes))
    assert stamp.calendars == ("https://stub.example/c",)


def test_stamp_dossier_rejects_empty_dossier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_calendars(monkeypatch)
    dossier = _build_dossier(payload_hashes=[])
    with pytest.raises(Exception) as exc:
        stamp_dossier(dossier, calendars=["https://stub.example/c"])
    assert "no evidence" in str(exc.value)


def test_dossier_timestamp_round_trips_through_json() -> None:
    stamp = DossierTimestamp(
        subject_id="subj-x",
        merkle_root_sha256="ab" * 32,
        receipt_b64="ZmFrZS1yZWNlaXB0",
        calendars=("https://stub.example/c",),
        leaf_hashes=("11" * 32, "22" * 32),
        stamped_at=datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC),
    )
    data = stamp.to_json()
    text = json.dumps(data)
    parsed = DossierTimestamp.from_json(json.loads(text))
    assert parsed == stamp


# ---------------------------------------------------------------------------
# verify_dossier
# ---------------------------------------------------------------------------


def test_verify_dossier_valid_after_clean_stamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_calendars(monkeypatch)
    hashes = [_hash(f"e-{i}".encode()) for i in range(4)]
    dossier = _build_dossier(payload_hashes=hashes)
    stamp = stamp_dossier(dossier, calendars=["https://stub.example/c"])

    result = verify_dossier(dossier, stamp)
    assert result.valid
    assert result.expected_root_sha256 == stamp.merkle_root_sha256
    assert result.pending_calendars == ("https://stub.example/c",)


def test_verify_dossier_is_snapshot_faithful_when_dossier_grows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_calendars(monkeypatch)
    hashes = [_hash(f"e-{i}".encode()) for i in range(3)]
    dossier = _build_dossier(payload_hashes=hashes)
    stamp = stamp_dossier(dossier, calendars=["https://stub.example/c"])

    # Append a new evidence row after stamping. Snapshot semantics
    # mean the receipt is STILL valid against the original leaf set.
    grown = _build_dossier(payload_hashes=[*hashes, _hash(b"new-evidence")])
    result = verify_dossier(grown, stamp)
    assert result.valid


def test_verify_dossier_detects_tampered_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_calendars(monkeypatch)
    hashes = [_hash(f"e-{i}".encode()) for i in range(2)]
    dossier = _build_dossier(payload_hashes=hashes)
    stamp = stamp_dossier(dossier, calendars=["https://stub.example/c"])
    # Pretend the persisted leaf list was edited to omit one hash
    # while keeping the original root. ``verify_dossier`` must spot
    # the inconsistency.
    tampered = DossierTimestamp(
        subject_id=stamp.subject_id,
        merkle_root_sha256=stamp.merkle_root_sha256,
        receipt_b64=stamp.receipt_b64,
        calendars=stamp.calendars,
        leaf_hashes=stamp.leaf_hashes[:1],  # dropped a leaf!
        stamped_at=stamp.stamped_at,
    )
    result = verify_dossier(dossier, tampered)
    assert not result.valid
