"""Bio similarity rule.

Phase 1 uses lexical cosine similarity on word-token frequency vectors —
deliberately simple, dependency-free, hermetic for tests. Phase 3 will swap
this for a sentence-transformers embedding pass behind the same `score()`
interface so callers do not change.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from ..confidence import ConfidenceContribution

WORD_RE = re.compile(r"\w+")


def tokenise(text: str) -> Counter[str]:
    """Lowercase + word-tokenise. Returns a token-frequency Counter."""
    return Counter(WORD_RE.findall(text.lower()))


def cosine(a: Counter[str], b: Counter[str]) -> float:
    """Cosine similarity of two token-frequency vectors. Range [0.0, 1.0]."""
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    num = sum(a[t] * b[t] for t in common)
    da = math.sqrt(sum(v * v for v in a.values()))
    db = math.sqrt(sum(v * v for v in b.values()))
    if da == 0.0 or db == 0.0:
        return 0.0
    return num / (da * db)


def score(
    bio_a: str | None,
    bio_b: str | None,
    *,
    threshold: float = 0.5,
) -> ConfidenceContribution | None:
    """Return a contribution iff the two bios cosine-overlap above `threshold`."""
    if not bio_a or not bio_b:
        return None
    s = cosine(tokenise(bio_a), tokenise(bio_b))
    if s < threshold:
        return None
    weight = min(0.7, 0.5 * s + 0.2)
    return ConfidenceContribution(
        rule="bio_similarity",
        weight=weight,
        reason=f"bio token-cosine similarity {s:.2f} (threshold {threshold:.2f})",
    )
