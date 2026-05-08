"""Tests for the AI reasoning layer (without hitting OpenAI)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from reckora.correlation.engine import correlate
from reckora.models.entity import Trace
from reckora.reasoning.client import ReasoningClient
from reckora.reasoning.hypothesize import hypothesize
from reckora.reasoning.summarize import format_edge, format_trace, summarize


def test_format_trace_includes_evidence_prefix(github_trace_alice: Trace) -> None:
    line = format_trace(github_trace_alice)
    assert "github_api" in line
    assert f"ev:{github_trace_alice.evidence.payload_sha256[:8]}" in line
    assert "alice" in line


def test_format_edge(github_trace_alice: Trace, web_trace_alice_twin: Trace) -> None:
    edges = correlate([github_trace_alice, web_trace_alice_twin])
    assert edges
    line = format_edge(edges[0])
    assert "conf=" in line
    assert "kind=" in line
    assert "reasons=" in line


async def test_reasoning_client_raises_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = ReasoningClient(api_key=None)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        await client.complete("system", "user")


async def test_summarize_calls_completion(
    github_trace_alice: Trace,
    web_trace_alice_twin: Trace,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_complete(system: str, user: str) -> str:
        captured["system"] = system
        captured["user"] = user
        return "summary text"

    client = ReasoningClient(api_key="ignored")
    client.complete = AsyncMock(side_effect=_fake_complete)  # type: ignore[method-assign]

    traces = [github_trace_alice, web_trace_alice_twin]
    edges = correlate(traces)
    out = await summarize(
        client,
        seed="username:alice",
        identifiers=["username:alice"],
        traces=traces,
        edges=edges,
    )
    assert out == "summary text"
    assert "Investigation summary request" in captured["user"]
    assert f"ev:{github_trace_alice.evidence.payload_sha256[:8]}" in captured["user"]


async def test_hypothesize_calls_completion(
    github_trace_alice: Trace,
    web_trace_alice_twin: Trace,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_complete(system: str, user: str) -> str:
        captured["user"] = user
        return "hypotheses"

    client = ReasoningClient(api_key="ignored")
    client.complete = AsyncMock(side_effect=_fake_complete)  # type: ignore[method-assign]

    traces = [github_trace_alice, web_trace_alice_twin]
    edges = correlate(traces)
    out = await hypothesize(
        client,
        seed="username:alice",
        identifiers=["username:alice"],
        traces=traces,
        edges=edges,
    )
    assert out == "hypotheses"
    assert "Hypothesis-generation request" in captured["user"]
