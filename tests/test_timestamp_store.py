"""Tests for the on-disk OpenTimestamps receipt store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from reckora.timestamp.receipt import DossierTimestamp
from reckora.timestamp.store import TimestampStore


def _stamp(subject_id: str = "subj-12345") -> DossierTimestamp:
    return DossierTimestamp(
        subject_id=subject_id,
        merkle_root_sha256="ab" * 32,
        receipt_b64="ZmFrZS1yZWNlaXB0",
        calendars=("https://stub.example/c",),
        leaf_hashes=("11" * 32, "22" * 32),
        stamped_at=datetime(2025, 1, 1, tzinfo=UTC),
    )


def test_store_creates_directory_on_init(tmp_path: Path) -> None:
    target = tmp_path / "does" / "not" / "exist-yet"
    TimestampStore(target)
    assert target.is_dir()


def test_save_and_load_round_trips(tmp_path: Path) -> None:
    store = TimestampStore(tmp_path)
    stamp = _stamp()
    path = store.save(stamp)
    assert path == tmp_path / "subj-12345.json"
    loaded = store.load("subj-12345")
    assert loaded == stamp


def test_load_missing_returns_none(tmp_path: Path) -> None:
    store = TimestampStore(tmp_path)
    assert store.load("does-not-exist") is None


def test_delete_returns_false_for_missing(tmp_path: Path) -> None:
    store = TimestampStore(tmp_path)
    assert store.delete("nope") is False


def test_delete_removes_existing_receipt(tmp_path: Path) -> None:
    store = TimestampStore(tmp_path)
    store.save(_stamp())
    assert store.delete("subj-12345") is True
    assert store.load("subj-12345") is None


def test_list_subject_ids_returns_sorted(tmp_path: Path) -> None:
    store = TimestampStore(tmp_path)
    store.save(_stamp("subj-zzz"))
    store.save(_stamp("subj-aaa"))
    store.save(_stamp("subj-mmm"))
    assert store.list_subject_ids() == ["subj-aaa", "subj-mmm", "subj-zzz"]


def test_save_overwrites_previous_receipt(tmp_path: Path) -> None:
    store = TimestampStore(tmp_path)
    store.save(_stamp())
    later = DossierTimestamp(
        subject_id="subj-12345",
        merkle_root_sha256="cd" * 32,
        receipt_b64="b3RoZXItcmVjZWlwdA==",
        calendars=("https://stub.example/c2",),
        leaf_hashes=("33" * 32,),
        stamped_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    store.save(later)
    loaded = store.load("subj-12345")
    assert loaded == later


@pytest.mark.parametrize(
    "evil_id",
    ["", ".", "..", "a/b", "a\\b"],
)
def test_save_rejects_path_traversal_subject_ids(tmp_path: Path, evil_id: str) -> None:
    store = TimestampStore(tmp_path)
    bad = DossierTimestamp(
        subject_id=evil_id,
        merkle_root_sha256="ab" * 32,
        receipt_b64="",
        calendars=(),
        leaf_hashes=(),
        stamped_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError):
        store.save(bad)
