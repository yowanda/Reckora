"""Tests for the NetworkX graph builder."""

from __future__ import annotations

from reckora.correlation.engine import correlate
from reckora.graph.build import build_graph, node_key
from reckora.models.entity import Trace


def test_build_graph_round_trips_edges(
    github_trace_alice: Trace, web_trace_alice_twin: Trace
) -> None:
    traces = [github_trace_alice, web_trace_alice_twin]
    edges = correlate(traces)
    graph = build_graph(edges)

    # Every edge endpoint becomes a node.
    for e in edges:
        assert node_key(e.source) in graph.nodes
        assert node_key(e.target) in graph.nodes

    # Edge attributes survive the round-trip.
    assert graph.number_of_edges() == len(edges)
    for _u, _v, data in graph.edges(data=True):
        assert "kind" in data
        assert "confidence" in data
        assert 0.0 < data["confidence"] <= 1.0
        assert isinstance(data["reasons"], list)
        assert isinstance(data["evidence"], list)


def test_build_graph_empty() -> None:
    graph = build_graph([])
    assert graph.number_of_nodes() == 0
    assert graph.number_of_edges() == 0
