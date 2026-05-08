"""Tests for the bio-similarity rule."""

from __future__ import annotations

import pytest

from reckora.correlation.rules.bio_similarity import cosine, score, tokenise


def test_tokenise_lowercases_and_splits() -> None:
    counter = tokenise("OSINT, Researcher 2026!")
    assert counter == {"osint": 1, "researcher": 1, "2026": 1}


def test_cosine_disjoint_is_zero() -> None:
    assert cosine(tokenise("alpha"), tokenise("beta")) == 0.0


def test_cosine_identical_is_one() -> None:
    a = tokenise("alpha beta gamma")
    assert cosine(a, a) == pytest.approx(1.0)


def test_cosine_partial() -> None:
    s = cosine(tokenise("alpha beta gamma"), tokenise("alpha beta delta"))
    assert 0.0 < s < 1.0


def test_score_above_threshold() -> None:
    contrib = score(
        "Security researcher and OSINT enthusiast.",
        "OSINT researcher; security and incident response.",
        threshold=0.3,
    )
    assert contrib is not None
    assert 0.0 < contrib.weight <= 0.7


def test_score_below_threshold_returns_none() -> None:
    assert score("alpha beta gamma", "x y z", threshold=0.5) is None


def test_score_none_inputs() -> None:
    assert score(None, "alpha") is None
    assert score("alpha", None) is None
    assert score("", "alpha") is None
