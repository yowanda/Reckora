"""Username-mutation rule.

Detects whether two usernames are plausibly the same handle written
differently — leetspeak, casing, separators, small typos. Operates on a
"normalised" form (lowercase, leet-substituted, alphanumerics only) and
falls back to `difflib.SequenceMatcher` for near-misses.

Out of scope for v1:
- transliteration (Cyrillic ↔ Latin)
- semantic equivalence ("john_smith" vs "jsmith")
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from ..confidence import ConfidenceContribution

LEET_MAP = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t"})
NON_ALNUM = re.compile(r"[^a-z0-9]")


def normalise(username: str) -> str:
    """Lowercase, drop separators, fold common leetspeak digits to letters."""
    return NON_ALNUM.sub("", username.lower().translate(LEET_MAP))


def score(username_a: str, username_b: str) -> ConfidenceContribution | None:
    """Return a contribution iff `username_a` and `username_b` are plausibly the same."""
    na, nb = normalise(username_a), normalise(username_b)
    if not na or not nb:
        return None
    if na == nb:
        return ConfidenceContribution(
            rule="username_mutation",
            weight=0.85,
            reason=f"normalised usernames match exactly ({na!r})",
        )
    ratio = SequenceMatcher(None, na, nb).ratio()
    if ratio >= 0.85:
        return ConfidenceContribution(
            rule="username_mutation",
            weight=0.6 * ratio,
            reason=(f"normalised usernames similar (ratio={ratio:.2f}: {na!r} ↔ {nb!r})"),
        )
    return None
