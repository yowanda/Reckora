"""Pure deterministic Merkle tree over evidence SHA-256 hashes.

The algorithm intentionally matches the one Bitcoin uses for its
block-level transaction commitment, with two adjustments suited to
Reckora's "audit a dossier later" use case:

* **Sorted leaves.** Bitcoin orders leaves by transaction index; a
  dossier doesn't have a meaningful index, and the same investigation
  re-run later may legitimately produce traces in a different order
  (collector parallelism, retry timing). Sorting the leaves
  ascending by their hex-encoded hash makes the root invariant under
  any benign re-ordering, so the receipt stays valid as long as the
  *set* of evidence hashes is unchanged.
* **Last-leaf duplication on odd rounds.** Same as Bitcoin (and the
  bug everyone knows about). Duplicating the last leaf when a round
  has an odd number of nodes keeps the tree balanced without
  introducing per-tree padding metadata. The CVE-2012-2459
  malleability concern doesn't apply here because the leaves are
  themselves SHA-256 outputs and the verifier always re-computes the
  whole root before trusting it.

We do **not** use OpenTimestamps' own ``make_merkle_tree`` for this
step. That helper builds a tree of ``Timestamp`` objects (which
embed *operations*, not just hashes), but we only need a single 32-
byte commitment to hand to the calendar. Keeping this layer pure
also makes it trivially testable without any network or third-party
state.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

# 32 zero bytes is the conventional sentinel for "no commitment". An
# empty dossier has no evidence to hash and therefore no Merkle root;
# we surface that case explicitly rather than silently hashing an
# empty string (which would produce ``e3b0...b855`` and look like a
# real, attackable commitment).
EMPTY_MERKLE_ROOT: bytes = b"\x00" * 32


def _double_sha256(data: bytes) -> bytes:
    """Bitcoin-style double-SHA256 (``SHA256(SHA256(x))``).

    Used for both leaf and internal-node hashing so the algorithm is
    symmetric and any re-implementation in another language can be
    spot-checked against ``bitcoin-cli``.
    """
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def leaves_from_hashes(payload_sha256_hex: Iterable[str]) -> list[bytes]:
    """Convert evidence hex hashes into raw 32-byte leaves.

    Empty / blank entries are dropped (defensive: a dossier shouldn't
    contain blank evidence rows, but we don't want a stray '' to
    silently corrupt the root). Duplicates are *kept* — a single
    underlying source URL referenced by multiple traces should
    contribute to the commitment exactly once per trace.
    """
    leaves: list[bytes] = []
    for h in payload_sha256_hex:
        if not h:
            continue
        leaves.append(bytes.fromhex(h))
    return leaves


def build_merkle_root(leaves: list[bytes]) -> bytes:
    """Compute the Merkle root over ``leaves``.

    Each leaf MUST already be a 32-byte SHA-256 digest. The function
    sorts the leaves ascending so the result is invariant under any
    permutation of the input (see module docstring).

    Returns :data:`EMPTY_MERKLE_ROOT` when ``leaves`` is empty so the
    caller can detect the "nothing to commit to" case without an
    extra branch on every result.
    """
    if not leaves:
        return EMPTY_MERKLE_ROOT
    for leaf in leaves:
        if len(leaf) != 32:
            raise ValueError(f"Merkle leaves must be 32-byte SHA-256 digests; got {len(leaf)}")
    # Sort ascending so re-ordered traces produce the same root.
    level: list[bytes] = sorted(leaves)
    while len(level) > 1:
        # Last-leaf duplication on odd rounds (Bitcoin convention).
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [_double_sha256(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]
