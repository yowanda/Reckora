"""Hypothesis-generation prompt runner."""

from __future__ import annotations

from ..models.entity import Edge, Trace
from .client import ReasoningClient
from .prompts import HYPOTHESIZE_USER_TEMPLATE, SYSTEM_BASE
from .summarize import format_edge, format_trace


async def hypothesize(
    client: ReasoningClient,
    *,
    seed: str,
    identifiers: list[str],
    traces: list[Trace],
    edges: list[Edge],
) -> str:
    """Run the hypothesis prompt and return the LLM's markdown output."""
    user = HYPOTHESIZE_USER_TEMPLATE.format(
        seed=seed,
        identifiers=identifiers,
        n_traces=len(traces),
        traces="\n".join(format_trace(t) for t in traces) or "(none)",
        n_edges=len(edges),
        edges="\n".join(format_edge(e) for e in edges) or "(none)",
    )
    return await client.complete(SYSTEM_BASE, user)
