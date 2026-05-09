"""Tool-using research phase that runs *before* the AgentLoop's plan.

The AgentLoop's planner (``_iterate``) used to be entirely passive: it
read whatever traces the collectors had produced and proposed
follow-up identifiers strictly from that set. That made the LLM at
best a re-ranker of evidence the rule-based engine had already
gathered. The Researcher widens that — it lets the LLM browse the
public web (via the builtin ``web_search`` and ``fetch_url`` tools)
*before* committing to a plan.

Every tool invocation produces a real :class:`Trace` rooted in
``TraceSource.WEB_RESEARCH`` with an :class:`Evidence` row whose
``payload_sha256`` is the canonical hash of the tool result. Those
traces are folded back into the AgentLoop's working set, so the
verifier's ``evidence_refs`` rule still applies — the AI can cite a
search hit it just discovered, not a fact it hallucinated.

The runner enforces a per-iteration :class:`ToolBudget` so a chatty
LLM cannot exhaust the network. When the budget is hit, subsequent
tool calls return an error result and the model is expected to
finalise its plan within one or two more turns.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from typing import Any

from ..models.entity import Edge, Identifier, Trace
from ..reasoning.client import AssistantTurn, ReasoningClient, ToolsNotSupportedError
from ..reasoning.summarize import format_edge, format_trace
from .prompts import RESEARCH_SYSTEM, RESEARCH_USER_TEMPLATE
from .tools import (
    ToolBudget,
    ToolInvocation,
    ToolResult,
    ToolRunSummary,
    ToolSpec,
    builtin_tools,
    run_tool,
)

log = logging.getLogger(__name__)

_DEFAULT_MAX_TURNS = 6


class Researcher:
    """Runs a tool-call conversation and returns materialised traces.

    Construction-time configuration is intentionally narrow: the
    inventory of tools the LLM may call, the global call budget, and
    a hard ``max_turns`` ceiling so the loop terminates even if the
    model keeps emitting tool calls forever.
    """

    def __init__(
        self,
        *,
        client: ReasoningClient,
        tools: Sequence[ToolSpec],
        budget: ToolBudget,
        max_turns: int = _DEFAULT_MAX_TURNS,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        self._client = client
        self._tools = list(tools)
        self._budget = budget
        self._max_turns = max_turns
        self._tool_index = {t.name: t for t in self._tools}

    @classmethod
    def with_default_tools(
        cls,
        *,
        client: ReasoningClient,
        seed: Identifier,
        budget: ToolBudget | None = None,
        max_turns: int = _DEFAULT_MAX_TURNS,
    ) -> Researcher:
        """Construct a Researcher pre-loaded with ``web_search`` + ``fetch_url``."""
        actual_budget = budget if budget is not None else ToolBudget()
        return cls(
            client=client,
            tools=builtin_tools(seed=seed, budget=actual_budget),
            budget=actual_budget,
            max_turns=max_turns,
        )

    async def run(
        self,
        *,
        seed: Identifier,
        iteration: int,
        max_iterations: int,
        traces: Sequence[Trace],
        edges: Sequence[Edge],
        visited: Iterable[Identifier],
    ) -> ToolRunSummary:
        """Run the tool-call loop until the model stops calling tools.

        Returns a :class:`ToolRunSummary` carrying every invocation
        and the materialised :class:`Trace` rows. The AgentLoop merges
        ``new_traces`` into its working set before it asks the planner
        for a JSON plan in the same iteration.
        """
        user_prompt = self._build_user_prompt(
            seed=seed,
            iteration=iteration,
            max_iterations=max_iterations,
            visited=visited,
            traces=traces,
            edges=edges,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": RESEARCH_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]
        tool_specs = [t.to_openai() for t in self._tools]
        invocations: list[ToolInvocation] = []
        new_traces: list[Trace] = []
        over_budget = False

        for _ in range(self._max_turns):
            try:
                turn = await self._client.chat_with_tools(
                    messages=messages,
                    tools=tool_specs,
                )
            except ToolsNotSupportedError:
                # Auth path can't drive function calls; surface up to
                # the AgentLoop, which falls back to its planner-only
                # behaviour.
                raise
            except Exception:
                log.exception("research turn failed")
                break

            messages.append(_assistant_message(turn))
            if not turn.tool_calls:
                break

            for call in turn.tool_calls:
                spec = self._tool_index.get(call.name)
                if spec is None:
                    result = ToolResult(
                        content={"error": f"unknown tool: {call.name}"},
                        error="unknown_tool",
                    )
                else:
                    result = await run_tool(
                        spec=spec,
                        arguments=call.arguments,
                        budget=self._budget,
                    )
                if result.error == "budget":
                    over_budget = True
                if result.trace is not None:
                    new_traces.append(result.trace)
                invocations.append(
                    ToolInvocation(
                        name=call.name,
                        arguments=call.arguments,
                        result=result,
                    )
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result.content)[:6000],
                    }
                )
        return ToolRunSummary(
            invocations=tuple(invocations),
            new_traces=tuple(new_traces),
            over_budget=over_budget,
        )

    def _build_user_prompt(
        self,
        *,
        seed: Identifier,
        iteration: int,
        max_iterations: int,
        visited: Iterable[Identifier],
        traces: Sequence[Trace],
        edges: Sequence[Edge],
    ) -> str:
        identifiers = sorted(str(i) for i in visited)
        return RESEARCH_USER_TEMPLATE.format(
            seed=str(seed),
            iteration=iteration,
            max_iterations=max_iterations,
            identifiers="\n".join(f"- {i}" for i in identifiers) or "(none)",
            n_traces=len(traces),
            traces="\n".join(format_trace(t) for t in traces) or "(none)",
            n_edges=len(edges),
            edges="\n".join(format_edge(e) for e in edges) or "(none)",
        )


def _assistant_message(turn: AssistantTurn) -> dict[str, Any]:
    """Render an :class:`AssistantTurn` back into chat-completions wire form."""
    msg: dict[str, Any] = {"role": "assistant", "content": turn.content or ""}
    if turn.tool_calls:
        msg["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in turn.tool_calls
        ]
    return msg
