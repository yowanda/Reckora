"""Tests for the AgentRouter LLM path in :class:`ReasoningClient`.

AgentRouter (https://agentrouter.org) is fronted by an Aliyun WAF
that allowlists requests by client fingerprint. Generic
OpenAI-Python-SDK calls are rejected (``unauthorized client
detected``) regardless of credential, **and so are async-Anthropic
SDK calls** -- only the *sync* Anthropic SDK fingerprint is on the
allowlist. The AgentRouter path therefore uses the synchronous
``Anthropic`` client dispatched in a worker thread via
``asyncio.to_thread``. Tests therefore mock the Anthropic wire
shape (``system`` parameter, ``content`` block array, ``tool_use``
blocks, etc.) on the sync client and use an
``MockTransport``-backed ``httpx.Client``.

Coverage:

* ``provider="agentrouter"`` routes through the AgentRouter base
  URL with the user's BYOK key, sends the configured model, and
  returns the assistant text.
* ``provider="agentrouter"`` raises a clear ``RuntimeError`` when no
  AgentRouter API key is configured anywhere.
* ``chat_with_tools`` under ``provider="agentrouter"`` translates
  the OpenAI-style messages/tools to the Anthropic shape and
  decodes ``tool_use`` content blocks back into
  :class:`AssistantToolCall`.
* ``provider="auto"`` continues to prefer ``OPENAI_API_KEY`` over
  the AgentRouter path so existing API-key deploys are untouched.
* ``provider="openai"`` and ``provider="chatgpt_oauth"`` raise when
  pinned but the corresponding credential is absent.
* The ``provider`` constructor argument validates eagerly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anthropic
import httpx
import pytest

from reckora.reasoning.client import ReasoningClient, ToolsNotSupportedError


def _make_recording_transport(
    *,
    captured: list[httpx.Request],
    text: str = "hi from agentrouter",
    tool_uses: list[dict[str, Any]] | None = None,
) -> httpx.MockTransport:
    """Record every request and return one fake Anthropic Message.

    The Anthropic SDK posts the model + messages on the request body;
    we echo the model back so tests can assert on the wire shape and
    optionally include ``tool_use`` content blocks to drive the
    function-calling decoder.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        body = json.loads(request.content.decode("utf-8"))
        content_blocks: list[dict[str, Any]] = []
        if text:
            content_blocks.append({"type": "text", "text": text})
        for tu in tool_uses or []:
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": tu["id"],
                    "name": tu["name"],
                    "input": tu.get("input", {}),
                }
            )
        return httpx.Response(
            200,
            json={
                "id": "msg_stub",
                "type": "message",
                "role": "assistant",
                "model": body["model"],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "content": content_blocks,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    return httpx.MockTransport(handler)


def _wire_mock_anthropic(client: ReasoningClient, transport: httpx.MockTransport) -> None:
    """Bypass lazy SDK construction by injecting a pre-built Anthropic client.

    Uses the **sync** ``httpx.Client`` (and therefore the **sync**
    ``Anthropic`` SDK) because that's the only fingerprint AgentRouter's
    WAF allows in production -- the production code path is sync-only,
    dispatched through ``asyncio.to_thread``.
    """
    http = httpx.Client(transport=transport)
    client._agentrouter_anthropic = anthropic.Anthropic(
        api_key="sk-byok-test",
        base_url="https://agentrouter.example/",
        http_client=http,
    )


@pytest.mark.asyncio
async def test_provider_agentrouter_routes_through_base_url() -> None:
    """provider='agentrouter' must hit the AgentRouter Messages API with BYOK key."""
    captured: list[httpx.Request] = []
    transport = _make_recording_transport(captured=captured, text="opus says hi")
    client = ReasoningClient(
        api_key=None,
        agentrouter_api_key="sk-byok-test",
        agentrouter_base_url="https://agentrouter.example/",
        agentrouter_model="claude-opus-4-6",
        provider="agentrouter",
    )
    _wire_mock_anthropic(client, transport)
    try:
        out = await client.complete("system", "user")
    finally:
        await client.aclose()

    assert out == "opus says hi"
    assert len(captured) == 1
    req = captured[0]
    assert str(req.url) == "https://agentrouter.example/v1/messages"
    assert req.headers["x-api-key"] == "sk-byok-test"
    body = json.loads(req.content.decode("utf-8"))
    assert body["model"] == "claude-opus-4-6"
    # System prompt is hoisted out of the messages array per Anthropic's contract.
    assert body["system"] == "system"
    assert body["messages"] == [{"role": "user", "content": "user"}]
    assert "max_tokens" in body  # Anthropic requires it.


@pytest.mark.asyncio
async def test_provider_agentrouter_raises_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin agentrouter without a key anywhere → loud RuntimeError."""
    monkeypatch.delenv("AGENTROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = ReasoningClient(api_key=None, provider="agentrouter")
    with pytest.raises(RuntimeError, match="AgentRouter"):
        await client.complete("s", "u")


@pytest.mark.asyncio
async def test_provider_agentrouter_chat_with_tools_decodes_calls() -> None:
    """Tool-call round-trip via AgentRouter must decode ``tool_use`` blocks."""
    captured: list[httpx.Request] = []
    transport = _make_recording_transport(
        captured=captured,
        text="",
        tool_uses=[
            {
                "id": "toolu_1",
                "name": "web_search",
                "input": {"query": "reckora"},
            }
        ],
    )
    client = ReasoningClient(
        api_key=None,
        agentrouter_api_key="sk-byok-test",
        agentrouter_base_url="https://agentrouter.example/",
        agentrouter_model="claude-opus-4-6",
        provider="agentrouter",
    )
    _wire_mock_anthropic(client, transport)
    try:
        turn = await client.chat_with_tools(
            messages=[
                {"role": "system", "content": "be terse"},
                {"role": "user", "content": "search reckora"},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "search the web",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )
    finally:
        await client.aclose()

    assert turn.content == ""
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].id == "toolu_1"
    assert turn.tool_calls[0].name == "web_search"
    assert turn.tool_calls[0].arguments == {"query": "reckora"}

    body = json.loads(captured[0].content.decode("utf-8"))
    # System prompt hoisted; tools translated to top-level Anthropic shape.
    assert body["system"] == "be terse"
    assert body["messages"] == [{"role": "user", "content": "search reckora"}]
    assert body["tools"] == [
        {
            "name": "web_search",
            "description": "search the web",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    assert body["tool_choice"] == {"type": "auto"}


@pytest.mark.asyncio
async def test_provider_agentrouter_chat_with_tools_round_trips_results() -> None:
    """``tool`` results must be encoded as ``tool_result`` content blocks."""
    captured: list[httpx.Request] = []
    transport = _make_recording_transport(captured=captured, text="search returned 0 hits")
    client = ReasoningClient(
        api_key=None,
        agentrouter_api_key="sk-byok-test",
        agentrouter_base_url="https://agentrouter.example/",
        agentrouter_model="claude-opus-4-6",
        provider="agentrouter",
    )
    _wire_mock_anthropic(client, transport)
    try:
        await client.chat_with_tools(
            messages=[
                {"role": "system", "content": "be terse"},
                {"role": "user", "content": "search reckora"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "toolu_42",
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "arguments": '{"query":"reckora"}',
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "toolu_42", "content": "no hits"},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "search the web",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )
    finally:
        await client.aclose()

    body = json.loads(captured[0].content.decode("utf-8"))
    # Assistant tool-calls round-trip into ``tool_use`` blocks; tool
    # result rows fold into a ``user`` message with a ``tool_result``
    # block referencing the same id.
    assistant_msg = body["messages"][1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["content"] == [
        {
            "type": "tool_use",
            "id": "toolu_42",
            "name": "web_search",
            "input": {"query": "reckora"},
        }
    ]
    tool_result_msg = body["messages"][2]
    assert tool_result_msg["role"] == "user"
    assert tool_result_msg["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "toolu_42",
            "content": "no hits",
        }
    ]


@pytest.mark.asyncio
async def test_provider_agentrouter_chat_with_tools_raises_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ToolsNotSupportedError when AgentRouter is pinned but no key set."""
    monkeypatch.delenv("AGENTROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = ReasoningClient(api_key=None, provider="agentrouter")
    with pytest.raises(ToolsNotSupportedError, match="AgentRouter"):
        await client.chat_with_tools(messages=[], tools=[])


def test_provider_auto_prefers_openai_api_key_over_agentrouter() -> None:
    """Historical contract: API key wins under provider='auto'."""
    client = ReasoningClient(
        api_key="sk-openai",
        agentrouter_api_key="sk-byok",
        provider="auto",
    )
    # No transport wired — the test only asserts the dispatch
    # decision via the persisted constructor state. The OAuth and
    # AgentRouter branches must not be reachable in this configuration.
    assert client._provider == "auto"
    assert client._api_key == "sk-openai"


@pytest.mark.asyncio
async def test_provider_openai_pinned_raises_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin OpenAI but no key anywhere → loud RuntimeError."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = ReasoningClient(api_key=None, provider="openai")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        await client.complete("s", "u")


@pytest.mark.asyncio
async def test_provider_chatgpt_oauth_pinned_raises_without_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pin ChatGPT OAuth but no creds → RuntimeError."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = ReasoningClient(
        api_key=None,
        provider="chatgpt_oauth",
        credentials_path=tmp_path / "missing.json",
    )
    with pytest.raises(RuntimeError, match="OAuth"):
        await client.complete("s", "u")


def test_invalid_provider_raises_eagerly() -> None:
    """Unknown provider name must surface at construction time."""
    with pytest.raises(ValueError, match="unknown provider"):
        ReasoningClient(provider="not-a-real-provider")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "configured",
    [
        "https://agentrouter.org/",
        "https://agentrouter.org",
        "https://agentrouter.org/v1",
        "https://agentrouter.org/v1/",
    ],
)
@pytest.mark.asyncio
async def test_agentrouter_base_url_strips_legacy_v1_suffix(configured: str) -> None:
    """Stale ``.../v1`` base URLs (carry-overs from the OpenAI-SDK path)
    must resolve to the same endpoint as the bare root, otherwise the
    Anthropic SDK appends ``/v1/messages`` and the request 404s with
    ``Invalid URL (POST /v1/v1/messages)``."""
    captured: list[httpx.Request] = []
    transport = _make_recording_transport(captured=captured, text="ok")
    client = ReasoningClient(
        api_key=None,
        agentrouter_api_key="sk-byok-test",
        agentrouter_base_url=configured,
        agentrouter_model="claude-opus-4-6",
        provider="agentrouter",
    )
    # Build the SDK client through the production code path so the
    # ``/v1``-suffix coercion runs, then swap its underlying httpx
    # transport to the in-memory mock so the request never leaves
    # the process.
    real_client = client._get_agentrouter_client()
    real_client._client = httpx.Client(transport=transport)
    try:
        await client.complete("system", "user")
    finally:
        await client.aclose()

    assert str(captured[0].url) == "https://agentrouter.org/v1/messages"
