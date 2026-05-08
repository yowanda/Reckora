"""Avatar perceptual-hash rule.

Two profiles using visually similar avatars are a strong signal of shared
identity. We use the dHash family — fast, robust to small re-encodings, and
already present in `imagehash`.

Inputs:
- 64-bit dHash hex strings (16 hex chars). Collectors are responsible for
  fetching the avatar bytes and computing the hash; we do not do I/O here.

Output:
- A `ConfidenceContribution` whose weight tapers from 0.95 (exact match) down
  to ~0.55 at the configured `max_distance` Hamming threshold.
"""

from __future__ import annotations

import io

import imagehash
from PIL import Image

from ..confidence import ConfidenceContribution


def hash_image_bytes(buf: bytes, *, hash_size: int = 8) -> str:
    """Compute a dHash hex string for image bytes."""
    img = Image.open(io.BytesIO(buf))
    return str(imagehash.dhash(img, hash_size=hash_size))


def hamming(hash_a: str, hash_b: str) -> int:
    """Hamming distance, in bits, between two equal-length hex hashes."""
    if len(hash_a) != len(hash_b):
        raise ValueError(f"hash length mismatch: {len(hash_a)} != {len(hash_b)}")
    diff = int(hash_a, 16) ^ int(hash_b, 16)
    return bin(diff).count("1")


def score(hash_a: str, hash_b: str, *, max_distance: int = 5) -> ConfidenceContribution | None:
    """Return a contribution iff the two pHashes are within `max_distance` bits."""
    try:
        d = hamming(hash_a, hash_b)
    except ValueError:
        return None
    if d > max_distance:
        return None
    weight = 0.95 if max_distance <= 0 else 0.95 - (d / max_distance) * 0.4
    return ConfidenceContribution(
        rule="avatar_phash",
        weight=max(0.0, min(0.95, weight)),
        reason=f"avatar perceptual hashes match within {d} bits (max={max_distance})",
    )
