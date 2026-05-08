"""Tests for the cross-trace Merkle tree (Layer 7)."""

from __future__ import annotations

import hashlib

import pytest

from reckora.evidence.chain import make_evidence
from reckora.evidence.merkle import compute_dossier_root, merkle_root, trace_leaves
from reckora.models.entity import Identifier, Trace
from reckora.models.enums import IdentifierType, TraceSource


def _leaf(value: str) -> str:
    """Helper: deterministic hex SHA-256 leaf from an arbitrary string."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _trace(seed: str, payload_key: str) -> Trace:
    return Trace(
        identifier=Identifier(type=IdentifierType.USERNAME, value=seed),
        source=TraceSource.WEB_PROFILE,
        fields={"platform": "test", "display_name": seed},
        evidence=make_evidence(f"https://x/{seed}", {"k": payload_key}),
    )


def test_merkle_root_is_64_char_hex() -> None:
    root = merkle_root([_leaf("a"), _leaf("b")])
    assert len(root) == 64
    assert all(c in "0123456789abcdef" for c in root)


def test_single_leaf_root_is_the_leaf_itself() -> None:
    leaf = _leaf("only")
    assert merkle_root([leaf]) == leaf


def test_root_is_independent_of_input_order() -> None:
    leaves = [_leaf(s) for s in ("alpha", "beta", "gamma", "delta")]
    assert merkle_root(leaves) == merkle_root(reversed(leaves))


def test_root_is_deterministic_across_calls() -> None:
    leaves = [_leaf(s) for s in ("p", "q", "r")]
    assert merkle_root(leaves) == merkle_root(leaves) == merkle_root(leaves)


def test_root_changes_when_any_leaf_changes() -> None:
    base = [_leaf("a"), _leaf("b"), _leaf("c")]
    tampered = [_leaf("a"), _leaf("b"), _leaf("c-tampered")]
    assert merkle_root(base) != merkle_root(tampered)


def test_odd_count_duplicates_last_node_bitcoin_style() -> None:
    """Three leaves: tree pads the right-most leaf, then hashes pairs."""
    a = _leaf("alpha")
    b = _leaf("beta")
    c = _leaf("gamma")
    sorted_hex = sorted([a, b, c])
    raw = [bytes.fromhex(h) for h in sorted_hex]
    # Manual Bitcoin-style tree: pad last leaf at level-0, hash pairs to 2 nodes,
    # pad again, hash pairs to a single root.
    level0 = [*raw, raw[-1]]
    level1 = [
        hashlib.sha256(level0[0] + level0[1]).digest(),
        hashlib.sha256(level0[2] + level0[3]).digest(),
    ]
    expected = hashlib.sha256(level1[0] + level1[1]).hexdigest()
    assert merkle_root([a, b, c]) == expected


def test_two_leaves_root_matches_single_sha256_pair() -> None:
    a = _leaf("a")
    b = _leaf("b")
    sorted_pair = sorted([a, b])
    expected = hashlib.sha256(
        bytes.fromhex(sorted_pair[0]) + bytes.fromhex(sorted_pair[1]),
    ).hexdigest()
    assert merkle_root([a, b]) == expected


def test_empty_iterable_raises_value_error() -> None:
    with pytest.raises(ValueError, match="at least one leaf"):
        merkle_root([])


@pytest.mark.parametrize(
    "bad_leaf",
    [
        "",
        "deadbeef",  # too short
        "z" * 64,  # not hex
        _leaf("a") + "00",  # too long
    ],
)
def test_malformed_leaf_raises_value_error(bad_leaf: str) -> None:
    with pytest.raises(ValueError):
        merkle_root([_leaf("ok"), bad_leaf])


def test_trace_leaves_preserves_input_order() -> None:
    traces = [_trace("alice", "p1"), _trace("bob", "p2"), _trace("carol", "p3")]
    leaves = trace_leaves(traces)
    assert leaves == [t.evidence.payload_sha256 for t in traces]


def test_compute_dossier_root_returns_sorted_leaves() -> None:
    traces = [_trace("alice", "p1"), _trace("bob", "p2"), _trace("carol", "p3")]
    root, leaves = compute_dossier_root(traces)
    assert leaves == sorted(t.evidence.payload_sha256 for t in traces)
    # Recomputing from the returned sorted leaves produces the same root.
    assert merkle_root(leaves) == root


def test_compute_dossier_root_empty_raises() -> None:
    with pytest.raises(ValueError, match="at least one trace"):
        compute_dossier_root([])
