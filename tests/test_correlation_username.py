"""Tests for the username-mutation rule."""

from __future__ import annotations

import pytest

from reckora.correlation.rules.username_mutation import normalise, score


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("alice", "alice"),
        ("Al1ce", "alice"),
        ("a_l_i_c_e", "alice"),
        ("ALICE!", "alice"),
        ("4l1c3", "alice"),
    ],
)
def test_normalise(raw: str, expected: str) -> None:
    assert normalise(raw) == expected


def test_score_exact_match_after_normalisation() -> None:
    contrib = score("Al1ce", "ALICE")
    assert contrib is not None
    assert contrib.weight == pytest.approx(0.85)
    assert "match exactly" in contrib.reason


def test_score_near_match() -> None:
    contrib = score("alice123", "alice124")
    assert contrib is not None
    assert 0.0 < contrib.weight <= 0.6
    assert "similar" in contrib.reason


def test_score_no_match() -> None:
    assert score("alice", "carlos") is None


def test_score_empty_inputs() -> None:
    assert score("", "alice") is None
    assert score("alice", "") is None
    assert score("***", "###") is None
