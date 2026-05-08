"""Build a NetworkX graph from correlation Edges."""

from __future__ import annotations

from collections.abc import Iterable

import networkx as nx

from ..models.entity import Edge, Identifier


def node_key(identifier: Identifier) -> str:
    """Stable, hashable string key for an Identifier in the graph."""
    return f"{identifier.type.value}:{identifier.value}"


def build_graph(edges: Iterable[Edge]) -> nx.MultiDiGraph[str]:
    """Materialise the Edge stream into a directed multigraph.

    Each Edge becomes one graph edge with `kind`, `confidence`, `reasons` and
    `evidence` (the list of supporting payload SHAs) attached as edge data.
    """
    graph: nx.MultiDiGraph[str] = nx.MultiDiGraph()
    for e in edges:
        for ident in (e.source, e.target):
            graph.add_node(
                node_key(ident),
                value=ident.value,
                type=ident.type.value,
            )
        graph.add_edge(
            node_key(e.source),
            node_key(e.target),
            kind=e.kind.value,
            confidence=e.confidence,
            reasons=list(e.reasons),
            evidence=list(e.supporting_evidence),
        )
    return graph
