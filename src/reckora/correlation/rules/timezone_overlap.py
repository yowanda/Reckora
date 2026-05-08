"""Timezone-overlap rule.

Inputs are flat lists of UTC hours-of-day at which an identifier was observed
to be active (collectors derive these from `created_at`, post timestamps, etc.).
We compute a normalised hour-of-day distribution per identifier and report
the histogram-overlap (sum of per-bucket minima) as a weak signal of shared
identity.

This is intentionally a weak rule — many people in the same timezone will
overlap heavily. The engine treats it as a tie-breaker rather than a primary
signal.
"""

from __future__ import annotations

from collections import Counter

from ..confidence import ConfidenceContribution


def hour_distribution(hours: list[int]) -> dict[int, float]:
    """Return a normalised hour-of-day histogram (sum to 1.0)."""
    if not hours:
        return {}
    counts = Counter(h % 24 for h in hours)
    total = float(sum(counts.values()))
    return {h: counts[h] / total for h in counts}


def overlap(d1: dict[int, float], d2: dict[int, float]) -> float:
    """Histogram overlap — sum of min per bucket. Range [0.0, 1.0]."""
    if not d1 or not d2:
        return 0.0
    keys = set(d1) | set(d2)
    return sum(min(d1.get(h, 0.0), d2.get(h, 0.0)) for h in keys)


def score(
    hours_a: list[int],
    hours_b: list[int],
    *,
    threshold: float = 0.5,
) -> ConfidenceContribution | None:
    """Return a contribution iff the two distributions overlap above `threshold`."""
    o = overlap(hour_distribution(hours_a), hour_distribution(hours_b))
    if o < threshold:
        return None
    return ConfidenceContribution(
        rule="timezone_overlap",
        weight=0.4 * o,
        reason=(f"activity-hour distributions overlap by {o:.2f} (threshold {threshold:.2f})"),
    )
