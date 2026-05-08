"""Confidence scoring primitives.

Every correlation rule emits zero or more `ConfidenceContribution`s. The
engine then collapses contributions per (pair, edge-kind) into a single
`Edge` whose `confidence` is the probabilistic-OR of the per-contribution
weights.

The probabilistic-OR is the standard "noisy-OR" fusion used in evidence-based
reasoning::

    P(same | s1, s2, ...) = 1 - prod_i (1 - P_i)

This treats independent positive signals as multiplicative-on-the-complement.
We do not model negative evidence in Phase 1; refutation will be added in a
later phase.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConfidenceContribution:
    """A single rule's contribution toward an Edge's confidence."""

    rule: str
    weight: float
    reason: str
    evidence_hashes: tuple[str, ...] = field(default_factory=tuple)


def combine(contribs: Iterable[ConfidenceContribution]) -> float:
    """Probabilistic-OR fusion of independent positive signals.

    The weight of each contribution is interpreted as the conditional
    probability that the rule firing implies same-identity. Weights are
    clamped into [0.0, 1.0] before fusion so a misbehaving rule cannot push
    the result outside the valid range.
    """
    p = 0.0
    for c in contribs:
        w = max(0.0, min(1.0, c.weight))
        p = p + (1.0 - p) * w
    return p
