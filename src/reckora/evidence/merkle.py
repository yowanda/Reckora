"""Cross-trace Merkle tree for tamper-evident dossier roots.

Each :class:`~reckora.models.entity.Trace` already carries a canonical
:attr:`Evidence.payload_sha256`. Layer 7 stitches those leaf hashes into a
single dossier-level *Merkle root* so a third party can timestamp one 32-byte
digest and inherit a guarantee that all evidence captured at that moment was
present unchanged.

The tree is intentionally boring:

* Leaves are the **hex SHA-256** strings copied verbatim from each trace's
  evidence record. Sorting them ascending makes the root a function of the
  *set* of evidence rather than insertion order, so two re-runs that pull the
  same payloads in different orders still anchor to the same digest.
* Inner nodes hash the concatenation of their two children's raw 32-byte
  digests with SHA-256. When a level has an odd node we duplicate the last
  one (Bitcoin-style) — simple, deterministic, and well-understood.
* The root for a single-leaf tree is just that leaf (no self-hash). That
  matches the "the digest is the root" intuition users already have for
  one-trace dossiers.

Anchoring (OpenTimestamps Calendar HTTP submission) lives in the sibling
:mod:`reckora.evidence.timestamp` module — this module stays pure-compute and
side-effect free so it is trivially testable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from ..models.entity import Trace


def _validate_leaf(leaf: str) -> bytes:
    """Decode one hex SHA-256 leaf into the raw 32 bytes the tree hashes over.

    Raises :class:`ValueError` for anything that is not exactly a 64-char
    lowercase-hex SHA-256 digest. The strictness keeps the Merkle root a
    pure function of well-formed evidence — a corrupted leaf in the input
    fails fast instead of silently producing an attacker-shaped root.
    """
    if len(leaf) != 64:
        raise ValueError(f"merkle leaf must be 64 hex chars, got {len(leaf)}: {leaf!r}")
    try:
        return bytes.fromhex(leaf)
    except ValueError as exc:
        raise ValueError(f"merkle leaf is not valid hex: {leaf!r}") from exc


def merkle_root(leaves: Iterable[str]) -> str:
    """Compute the dossier Merkle root over an iterable of hex SHA-256 leaves.

    Returns the root as a 64-char lowercase hex string. Raises
    :class:`ValueError` for an empty iterable — a Merkle root over zero
    evidence rows is meaningless and would be an easy footgun for callers
    that forget to guard the empty case.
    """
    raw = [_validate_leaf(leaf) for leaf in sorted(leaves)]
    if not raw:
        raise ValueError("merkle_root requires at least one leaf")

    level = raw
    while len(level) > 1:
        if len(level) % 2 == 1:
            level = [*level, level[-1]]
        level = [hashlib.sha256(level[i] + level[i + 1]).digest() for i in range(0, len(level), 2)]
    return level[0].hex()


def trace_leaves(traces: Iterable[Trace]) -> list[str]:
    """Extract the canonical hex SHA-256 leaves from each trace.

    The returned list preserves the trace order so callers can render
    "leaf N corresponds to trace N" alongside the root if they want to.
    The actual root computation re-sorts internally so order does not
    affect the digest.
    """
    return [t.evidence.payload_sha256 for t in traces]


def compute_dossier_root(traces: Iterable[Trace]) -> tuple[str, list[str]]:
    """Convenience helper: extract leaves from traces and compute the root.

    Returns ``(merkle_root_hex, sorted_leaves_hex)``. The second tuple
    element is the *sorted* leaf list — i.e. the actual byte sequence the
    root commits to, which is what downstream verifiers must hash over to
    re-derive the root.
    """
    leaves = trace_leaves(traces)
    if not leaves:
        raise ValueError("compute_dossier_root requires at least one trace")
    return merkle_root(leaves), sorted(leaves)
