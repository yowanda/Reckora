"""Tests for the timezone-overlap rule."""

from __future__ import annotations

import pytest

from reckora.correlation.rules.timezone_overlap import (
    hour_distribution,
    overlap,
    score,
)


def test_hour_distribution_normalises() -> None:
    d = hour_distribution([0, 0, 12])
    assert d[0] == pytest.approx(2 / 3)
    assert d[12] == pytest.approx(1 / 3)
    assert sum(d.values()) == pytest.approx(1.0)


def test_hour_distribution_empty() -> None:
    assert hour_distribution([]) == {}


def test_hour_distribution_modulo_24() -> None:
    d = hour_distribution([24, 25, 1])
    assert d[0] == pytest.approx(1 / 3)
    assert d[1] == pytest.approx(2 / 3)


def test_overlap_disjoint_is_zero() -> None:
    assert overlap({0: 1.0}, {12: 1.0}) == 0.0


def test_overlap_identical_is_one() -> None:
    d = {0: 0.5, 12: 0.5}
    assert overlap(d, d) == pytest.approx(1.0)


def test_score_above_threshold() -> None:
    contrib = score([9, 10, 11, 12], [10, 11, 12, 13], threshold=0.5)
    assert contrib is not None
    assert contrib.weight > 0.0
    assert "overlap" in contrib.reason


def test_score_below_threshold_returns_none() -> None:
    assert score([0, 1, 2], [12, 13, 14], threshold=0.5) is None


def test_score_empty_returns_none() -> None:
    assert score([], [10, 11], threshold=0.5) is None
