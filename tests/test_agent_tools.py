"""Tests for :mod:`reckora.agent.tools` and :mod:`reckora.agent.research`.

Covers:

* The builtin tool inventory (``web_search`` / ``fetch_url``) parses
  DDG HTML and HTML page bodies correctly and produces traces with a
  stable :class:`reckora.evidence.chain` SHA-256.
* :class:`ToolBudget` actually short-circuits subsequent calls when
  exhausted and surfaces an error :class:`ToolResult` rather than
  raising.
* The :class:`Researcher` orchestrator drives the LLM tool-loop:
  multi-turn assistant -> tool -> assistant flow, terminates on a
  plain assistant response, and returns the materialised traces +
  invocation log so the AgentLoop can record them on the transcript.
* :class:`AgentLoop` integrates the researcher: research traces show
  up in the transcript step, are folded into the working set before
  the planner runs, and the loop tolerates a researcher that raises
  :class:`ToolsNotSupportedError` by gracefully disabling itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar

import httpx
import pytest

from reckora.agent.loop import AgentLoop
from reckora.agent.research import Researcher
from reckora.agent.tools import (
    ToolBudget,
    ToolSpec,
    builtin_tools,
    run_tool,
)
from reckora.collectors.base import Collector
from reckora.evidence.chain import hash_payload, make_evidence
from reckora.models.entity import Identifier, Trace
from reckora.models.enums import IdentifierType, TraceSource
from reckora.orchestrator import Orchestrator
from reckora.reasoning.client import (
    AssistantToolCall,
    AssistantTurn,
    ToolsNotSupportedError,
)

DDG_HTML_FIXTURE = """\
<html><body>
<div class="result">
  <a class="result__a" href="https://example.com/alice">Alice's site</a>
  <a class="result__snippet" href="#">Personal homepage of Alice the researcher.</a>
</div>
<div class="result">
  <a class="result__a" href="https://forum.example.org/u/alice">Forum profile</a>
  <a class="result__snippet" href="#">Alice posts about cryptography here.</a>
</div>
</body></html>
"""


PAGE_HTML_FIXTURE = """\
<html>
<head><title>Alice — Researcher</title></head>
<body>
<script>var hidden = 1;</script>
<style>.x{display:none}</style>
<p>Alice is a security researcher based in Bandung.</p>
<p>Contact: <a href="mailto:alice@example.com">alice@example.com</a></p>
</body>
</html>
"""


SEED = Identifier(type=IdentifierType.USERNAME, value="alice")


def _client_factory(
    handler: httpx.MockTransport,
) -> Any:
    async def _make() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=handler)

    return _make


# ---------------------------------------------------------------------------
# tool-level tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_parses_ddg_results_and_emits_trace() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=DDG_HTML_FIXTURE)

    transport = httpx.MockTransport(respond)
    budget = ToolBudget()
    tools = builtin_tools(seed=SEED, budget=budget, client_factory=_client_factory(transport))
    spec = next(t for t in tools if t.name == "web_search")

    result = await run_tool(
        spec=spec,
        arguments={"query": "alice researcher", "max_results": 5},
        budget=budget,
    )

    assert result.error is None
    assert result.trace is not None
    assert result.trace.source == TraceSource.WEB_RESEARCH
    assert result.trace.fields["tool"] == "web_search"
    assert result.trace.fields["query"] == "alice researcher"
    assert result.content["results"][0]["url"] == "https://example.com/alice"
    assert "evidence_sha256" in result.content
    # SHA-256 must be deterministic across runs for the same payload.
    expected_sha = hash_payload(
        {
            "query": "alice researcher",
            "results": result.content["results"],
        }
    )
    assert result.trace.evidence.payload_sha256 == expected_sha
    # Budget must have been decremented by exactly one call.
    assert budget.calls_remaining == 7
    # DDG endpoint was hit once.
    assert len(requests) == 1
    assert "duckduckgo.com" in str(requests[0].url)


@pytest.mark.asyncio
async def test_fetch_url_strips_scripts_and_caps_text() -> None:
    target = "https://example.com/alice"

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == target
        return httpx.Response(200, text=PAGE_HTML_FIXTURE)

    transport = httpx.MockTransport(respond)
    budget = ToolBudget()
    tools = builtin_tools(seed=SEED, budget=budget, client_factory=_client_factory(transport))
    spec = next(t for t in tools if t.name == "fetch_url")

    result = await run_tool(spec=spec, arguments={"url": target}, budget=budget)

    assert result.error is None
    assert result.trace is not None
    text = result.content["text"]
    # Script + style removed.
    assert "var hidden" not in text
    assert "display:none" not in text
    # Body content preserved.
    assert "security researcher" in text
    # Title extracted.
    assert result.content["title"] == "Alice — Researcher"
    # Trace identifier coerced to the typed URL identifier so
    # correlation can fire later.
    assert result.trace.identifier.type == IdentifierType.URL
    assert result.trace.identifier.value == target


@pytest.mark.asyncio
async def test_fetch_url_rejects_non_http_scheme() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(500))
    budget = ToolBudget()
    tools = builtin_tools(seed=SEED, budget=budget, client_factory=_client_factory(transport))
    spec = next(t for t in tools if t.name == "fetch_url")

    result = await run_tool(
        spec=spec,
        arguments={"url": "ftp://example.com/x"},
        budget=budget,
    )
    assert result.error == "unsupported scheme"


@pytest.mark.asyncio
async def test_tool_budget_exhaustion_returns_error() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text="ok"))
    budget = ToolBudget(calls_remaining=1)
    tools = builtin_tools(seed=SEED, budget=budget, client_factory=_client_factory(transport))
    spec = next(t for t in tools if t.name == "web_search")

    first = await run_tool(spec=spec, arguments={"query": "alice"}, budget=budget)
    second = await run_tool(spec=spec, arguments={"query": "alice2"}, budget=budget)

    assert first.error is None  # First call burned the budget.
    assert second.error == "budget"
    assert second.content == {"error": "tool budget exhausted for this iteration"}


# ---------------------------------------------------------------------------
# researcher integration tests
# ---------------------------------------------------------------------------


class _ToolUsingClient:
    """Stand-in for :class:`ReasoningClient` used by Researcher tests.

    Plays back a scripted list of :class:`AssistantTurn` objects.
    Records every (messages, tools) tuple it received so tests can
    assert on the conversation shape — important to confirm the
    Researcher actually appends tool result messages between turns.
    """

    def __init__(self, turns: Iterable[AssistantTurn]) -> None:
        self._turns = list(turns)
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

    async def chat_with_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
    ) -> AssistantTurn:
        self.calls.append((list(messages), list(tools)))
        if not self._turns:
            return AssistantTurn(content="done")
        return self._turns.pop(0)


def _stub_search_tool(
    *,
    seed: Identifier,
    captured: list[dict[str, Any]],
) -> ToolSpec:
    """A tool spec whose handler records calls without going to the network."""
    from reckora.agent.tools import ToolResult

    async def handler(args: dict[str, Any]) -> ToolResult:
        captured.append(args)
        payload = {"query": args.get("query", ""), "results": [{"url": "u", "title": "t"}]}
        evidence = make_evidence("https://stub.test/q", payload)
        trace = Trace(
            identifier=seed,
            source=TraceSource.WEB_RESEARCH,
            fields={"tool": "stub_search", "query": args.get("query", "")},
            evidence=evidence,
        )
        return ToolResult(
            content={**payload, "evidence_sha256": evidence.payload_sha256},
            trace=trace,
        )

    return ToolSpec(
        name="stub_search",
        description="stub",
        schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=handler,
    )


@pytest.mark.asyncio
async def test_researcher_runs_tool_loop_until_assistant_stops() -> None:
    captured: list[dict[str, Any]] = []
    spec = _stub_search_tool(seed=SEED, captured=captured)
    client = _ToolUsingClient(
        [
            AssistantTurn(
                content="",
                tool_calls=(
                    AssistantToolCall(
                        id="call_1",
                        name="stub_search",
                        arguments={"query": "alice"},
                    ),
                ),
            ),
            AssistantTurn(content="No further research needed"),
        ]
    )
    researcher = Researcher(
        client=client,  # type: ignore[arg-type]
        tools=[spec],
        budget=ToolBudget(calls_remaining=4),
    )

    summary = await researcher.run(
        seed=SEED,
        iteration=1,
        max_iterations=3,
        traces=[],
        edges=[],
        visited=[SEED],
    )

    # The stub got called exactly once with the query the LLM asked for.
    assert captured == [{"query": "alice"}]
    # One invocation, one materialised trace.
    assert len(summary.invocations) == 1
    assert summary.invocations[0].name == "stub_search"
    assert len(summary.new_traces) == 1
    # The trace carries WEB_RESEARCH source, not the stub_search name —
    # source is a closed enum, the tool just decorates the fields.
    assert summary.new_traces[0].source == TraceSource.WEB_RESEARCH
    # Two LLM round-trips: the tool-call turn + the closing turn.
    assert len(client.calls) == 2
    # The second call must include the tool result message in its
    # conversation history so the model can decide to stop.
    second_messages = client.calls[1][0]
    assert any(m.get("role") == "tool" for m in second_messages)


@pytest.mark.asyncio
async def test_researcher_unknown_tool_records_error() -> None:
    spec = _stub_search_tool(seed=SEED, captured=[])
    client = _ToolUsingClient(
        [
            AssistantTurn(
                content="",
                tool_calls=(
                    AssistantToolCall(
                        id="call_x",
                        name="not_a_tool",
                        arguments={"q": "x"},
                    ),
                ),
            ),
            AssistantTurn(content="ok"),
        ]
    )
    researcher = Researcher(
        client=client,  # type: ignore[arg-type]
        tools=[spec],
        budget=ToolBudget(),
    )
    summary = await researcher.run(
        seed=SEED,
        iteration=1,
        max_iterations=2,
        traces=[],
        edges=[],
        visited=[SEED],
    )
    assert summary.invocations[0].result.error == "unknown_tool"
    # No trace emitted for an unknown tool.
    assert summary.new_traces == ()


# ---------------------------------------------------------------------------
# AgentLoop integration
# ---------------------------------------------------------------------------


class _ScriptedComplete:
    """Reasoning client double that satisfies both ``complete`` and
    ``chat_with_tools`` so we can drive the AgentLoop end-to-end with a
    researcher attached."""

    def __init__(
        self,
        *,
        plans: Iterable[str],
        turns: Iterable[AssistantTurn],
    ) -> None:
        self._plans = list(plans)
        self._turns = list(turns)

    async def complete(self, system: str, user: str) -> str:
        if not self._plans:
            return '{"plan": []}'
        return self._plans.pop(0)

    async def chat_with_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
    ) -> AssistantTurn:
        if not self._turns:
            return AssistantTurn(content="stop")
        return self._turns.pop(0)


class _NoopCollector(Collector):
    name: ClassVar[str] = "noop"
    supported: ClassVar[frozenset[str]] = frozenset(
        {IdentifierType.USERNAME.value, IdentifierType.URL.value}
    )

    async def collect(self, identifier: Identifier) -> list[Trace]:
        return []


@pytest.mark.asyncio
async def test_agent_loop_records_research_traces_in_transcript() -> None:
    captured: list[dict[str, Any]] = []
    spec = _stub_search_tool(seed=SEED, captured=captured)

    client = _ScriptedComplete(
        plans=['{"plan": []}'],
        turns=[
            AssistantTurn(
                content="",
                tool_calls=(
                    AssistantToolCall(
                        id="c1",
                        name="stub_search",
                        arguments={"query": "alice"},
                    ),
                ),
            ),
            AssistantTurn(content="No further research needed"),
        ],
    )
    researcher = Researcher(
        client=client,  # type: ignore[arg-type]
        tools=[spec],
        budget=ToolBudget(calls_remaining=4),
    )
    orchestrator = Orchestrator([_NoopCollector()])
    loop = AgentLoop(
        orchestrator,
        client,  # type: ignore[arg-type]
        max_iterations=2,
        researcher=researcher,
    )

    result = await loop.run(SEED)

    # An empty plan would normally produce no transcript step, but
    # because the researcher emitted a trace this iteration we keep
    # the step so the dossier UI can show what was looked at.
    assert len(result.transcript) == 1
    step = result.transcript[0]
    assert len(step.research_traces) == 1
    assert step.research_traces[0].source == TraceSource.WEB_RESEARCH
    assert any(t.source == TraceSource.WEB_RESEARCH for t in result.traces)


@pytest.mark.asyncio
async def test_agent_loop_disables_researcher_on_tools_not_supported() -> None:
    class _RaisingClient:
        async def complete(self, system: str, user: str) -> str:
            return '{"plan": []}'

        async def chat_with_tools(
            self,
            *,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]],
            tool_choice: str = "auto",
        ) -> AssistantTurn:
            raise ToolsNotSupportedError("oauth path")

    raising = _RaisingClient()
    spec = _stub_search_tool(seed=SEED, captured=[])
    researcher = Researcher(
        client=raising,  # type: ignore[arg-type]
        tools=[spec],
        budget=ToolBudget(),
    )
    orchestrator = Orchestrator([_NoopCollector()])
    loop = AgentLoop(
        orchestrator,
        raising,  # type: ignore[arg-type]
        max_iterations=2,
        researcher=researcher,
    )

    result = await loop.run(SEED)
    # Loop completed without crashing; the planner saw an empty plan
    # and stopped. No research traces produced.
    assert all(t.source != TraceSource.WEB_RESEARCH for t in result.traces)
    assert result.transcript == ()
