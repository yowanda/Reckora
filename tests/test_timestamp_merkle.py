"""Tests for the pure deterministic Merkle root builder.

The algorithm is documented in ``reckora.timestamp.merkle``: sorted
leaves, last-leaf duplication on odd rounds, double-SHA256 internal
nodes. These tests pin the exact mathematical contract — anything
that breaks them would silently invalidate every previously-issued
OpenTimestamps receipt, so every change here MUST be a conscious
protocol bump.
"""

from __future__ import annotations

import hashlib

import pytest

from reckora.timestamp.merkle import (
    EMPTY_MERKLE_ROOT,
    build_merkle_root,
    leaves_from_hashes,
)


def _sha(data: bytes) -> bytes:
    """Single-SHA256 helper (the leaf inputs themselves; not double)."""
    return hashlib.sha256(data).digest()


def _double(a: bytes, b: bytes) -> bytes:
    """Bitcoin-style double-SHA256 of two concatenated nodes."""
    return hashlib.sha256(hashlib.sha256(a + b).digest()).digest()


# ---------------------------------------------------------------------------
# leaves_from_hashes
# ---------------------------------------------------------------------------


def test_leaves_from_hashes_decodes_hex_to_32_byte_leaves() -> None:
    leaves = leaves_from_hashes(["00" * 32, "ff" * 32])
    assert leaves == [b"\x00" * 32, b"\xff" * 32]


def test_leaves_from_hashes_skips_blank_entries() -> None:
    # A blank evidence row shouldn't contribute to the commitment.
    leaves = leaves_from_hashes(["", "11" * 32, "", "22" * 32])
    assert leaves == [b"\x11" * 32, b"\x22" * 32]


def test_leaves_from_hashes_keeps_duplicates() -> None:
    # If two traces happen to point at the same source URL with the
    # same payload, they should both contribute to the root.
    leaves = leaves_from_hashes(["33" * 32, "33" * 32])
    assert leaves == [b"\x33" * 32, b"\x33" * 32]


def test_leaves_from_hashes_rejects_non_hex_input() -> None:
    with pytest.raises(ValueError):
        leaves_from_hashes(["not-hex"])


# ---------------------------------------------------------------------------
# build_merkle_root
# ---------------------------------------------------------------------------


def test_build_merkle_root_empty_returns_zero_sentinel() -> None:
    assert build_merkle_root([]) == EMPTY_MERKLE_ROOT


def test_build_merkle_root_single_leaf_returns_leaf() -> None:
    leaf = _sha(b"only-evidence")
    assert build_merkle_root([leaf]) == leaf


def test_build_merkle_root_two_leaves_double_sha256_concat() -> None:
    a = _sha(b"a")
    b = _sha(b"b")
    # Sorted ascending: a < b in this case (deterministic byte order).
    sorted_leaves = sorted([a, b])
    expected = _double(sorted_leaves[0], sorted_leaves[1])
    assert build_merkle_root([a, b]) == expected


def test_build_merkle_root_is_invariant_under_permutation() -> None:
    # The whole point of sorted leaves: re-running an investigation
    # in a different order MUST produce the same root.
    leaves = [_sha(f"evidence-{i}".encode()) for i in range(5)]
    assert build_merkle_root(leaves) == build_merkle_root(list(reversed(leaves)))


def test_build_merkle_root_three_leaves_uses_last_leaf_duplication() -> None:
    a = _sha(b"a")
    b = _sha(b"b")
    c = _sha(b"c")
    s = sorted([a, b, c])
    # Round 1: pad the odd third leaf with a copy of itself.
    n0 = _double(s[0], s[1])
    n1 = _double(s[2], s[2])
    # Round 2: now two nodes; combine them.
    expected = _double(n0, n1)
    assert build_merkle_root([a, b, c]) == expected


def test_build_merkle_root_four_leaves_balanced_tree() -> None:
    leaves = [_sha(f"evidence-{i}".encode()) for i in range(4)]
    s = sorted(leaves)
    n0 = _double(s[0], s[1])
    n1 = _double(s[2], s[3])
    expected = _double(n0, n1)
    assert build_merkle_root(leaves) == expected


def test_build_merkle_root_rejects_non_32_byte_leaf() -> None:
    with pytest.raises(ValueError):
        build_merkle_root([b"\x00" * 31])


def test_build_merkle_root_is_pure_and_deterministic() -> None:
    # Multiple runs over the same input MUST produce the same bytes.
    leaves = [_sha(f"evidence-{i}".encode()) for i in range(7)]
    runs = {build_merkle_root(leaves) for _ in range(10)}
    assert len(runs) == 1


def test_build_merkle_root_root_changes_when_a_leaf_is_swapped() -> None:
    leaves = [_sha(f"evidence-{i}".encode()) for i in range(4)]
    tampered = leaves[:]
    tampered[0] = _sha(b"tampered")
    assert build_merkle_root(leaves) != build_merkle_root(tampered)


def test_build_merkle_root_does_not_mutate_caller_list() -> None:
    leaves = [_sha(b"b"), _sha(b"a")]
    snapshot = list(leaves)
    build_merkle_root(leaves)
    assert leaves == snapshot
