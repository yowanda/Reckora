"""Tests for the evidence chain."""

from __future__ import annotations

from datetime import UTC, datetime

from reckora.evidence.chain import canonical_payload, hash_payload, make_evidence


def test_canonical_payload_is_stable_under_key_order() -> None:
    a = canonical_payload({"a": 1, "b": [3, 2, 1], "c": {"x": 1, "y": 2}})
    b = canonical_payload({"c": {"y": 2, "x": 1}, "b": [3, 2, 1], "a": 1})
    assert a == b


def test_hash_payload_returns_64_hex_chars() -> None:
    h = hash_payload({"a": 1})
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_payload_changes_on_value_change() -> None:
    assert hash_payload({"a": 1}) != hash_payload({"a": 2})


def test_make_evidence_keep_raw() -> None:
    payload = {"hello": "world"}
    ev = make_evidence("https://x", payload)
    assert ev.raw_payload == payload
    assert ev.payload_sha256 == hash_payload(payload)
    assert ev.source_url == "https://x"


def test_make_evidence_drop_raw() -> None:
    ev = make_evidence("https://x", {"k": "v"}, keep_raw=False)
    assert ev.raw_payload is None


def test_make_evidence_with_explicit_timestamp() -> None:
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    ev = make_evidence("https://x", {}, fetched_at=ts)
    assert ev.fetched_at == ts
