"""Investigation-summary generation."""

from __future__ import annotations

from ..models.entity import Edge, Trace
from .client import ReasoningClient
from .prompts import SUMMARIZE_USER_TEMPLATE, SYSTEM_BASE


def format_trace(trace: Trace) -> str:
    """Render a Trace as a single human-readable line for the LLM prompt."""
    keep = {k: v for k, v in trace.fields.items() if v not in (None, "", [])}
    return (
        f"- [{trace.source.value}] id={trace.identifier} "
        f"ev:{trace.evidence.payload_sha256[:8]} fields={keep}"
    )


def format_edge(edge: Edge) -> str:
    """Render an Edge as a single human-readable line for the LLM prompt."""
    return (
        f"- {edge.source} -> {edge.target} "
        f"kind={edge.kind.value} conf={edge.confidence:.2f} reasons={edge.reasons}"
    )


async def summarize(
    client: ReasoningClient,
    *,
    seed: str,
    identifiers: list[str],
    traces: list[Trace],
    edges: list[Edge],
) -> str:
    """Run the summary prompt and return the LLM's markdown output."""
    user = SUMMARIZE_USER_TEMPLATE.format(
        seed=seed,
        identifiers=identifiers,
        n_traces=len(traces),
        traces="\n".join(format_trace(t) for t in traces) or "(none)",
        n_edges=len(edges),
        edges="\n".join(format_edge(e) for e in edges) or "(none)",
    )
    return await client.complete(SYSTEM_BASE, user)
