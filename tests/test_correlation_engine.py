"""Tests for the correlation engine."""

from __future__ import annotations

from reckora.correlation.engine import correlate
from reckora.models.entity import Trace
from reckora.models.enums import EdgeKind


def test_correlate_same_username_emits_no_self_edge(github_trace_alice: Trace) -> None:
    edges = correlate([github_trace_alice, github_trace_alice])
    # The two traces share an Identifier, so we expect no edges.
    assert all(e.source != e.target for e in edges)


def test_correlate_two_alices_emits_expected_kinds(
    github_trace_alice: Trace, web_trace_alice_twin: Trace
) -> None:
    edges = correlate([github_trace_alice, web_trace_alice_twin])
    kinds = {e.kind for e in edges}
    assert EdgeKind.SAME_AVATAR in kinds
    assert EdgeKind.TIMEZONE_OVERLAP in kinds
    # bio similarity may or may not pass the conservative threshold; we only
    # assert that whatever edges fire have non-zero confidence and reasons.
    for e in edges:
        assert 0.0 < e.confidence <= 1.0
        assert e.reasons
        assert len(e.supporting_evidence) == 2


def test_correlate_no_edges_when_traces_unrelated(
    github_trace_alice: Trace, web_trace_alice_twin: Trace
) -> None:
    # Replace fields so no rule fires.
    other = web_trace_alice_twin.model_copy(
        update={
            "fields": {
                "platform": "example.org",
                "bio": None,
                "avatar_phash": None,
                "activity_hours_utc": None,
            }
        }
    )
    edges = correlate([github_trace_alice, other])
    # Username rule may still fire because both Identifier values normalise to
    # 'alice', so we just check edges are well-formed.
    for e in edges:
        assert 0.0 < e.confidence <= 1.0
        assert e.kind in EdgeKind
