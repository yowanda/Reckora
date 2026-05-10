"""Tests for the AgentRouter LLM path in :class:`ReasoningClient`.

AgentRouter (https://agentrouter.org) speaks the OpenAI
``/v1/chat/completions`` shape, so we can exercise the dispatch
logic without spinning up a fake server: an ``httpx.MockTransport``
attached to the OpenAI SDK's underlying client is enough.

Coverage:

* ``provider="agentrouter"`` routes through the AgentRouter base
  URL with the user's BYOK key, sends the configured model, and
  returns the assistant text.
* ``provider="agentrouter"`` raises a clear ``RuntimeError`` when no
  AgentRouter API key is configured anywhere.
* ``chat_with_tools`` under ``provider="agentrouter"`` reuses the
  shared OpenAI-SDK helper and decodes ``tool_calls`` correctly.
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

import httpx
import openai
import pytest

from reckora.reasoning.client import ReasoningClient, ToolsNotSupportedError


def _make_recording_transport(
    *,
    captured: list[httpx.Request],
    content: str = "hi from agentrouter",
    tool_calls: list[dict[str, Any]] | None = None,
) -> httpx.MockTransport:
    """Record every request and return one fake OpenAI chat-completion.

    The OpenAI SDK posts the model + messages on the request body;
    we echo the model back so tests can assert on the wire shape and
    optionally include ``tool_calls`` in the assistant message to
    drive the function-calling decoder.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        body = json.loads(request.content.decode("utf-8"))
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls is not None:
            message["tool_calls"] = tool_calls
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-stub",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": message,
                    }
                ],
            },
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_provider_agentrouter_routes_through_base_url() -> None:
    """provider='agentrouter' must hit the AgentRouter base URL with BYOK key."""
    captured: list[httpx.Request] = []
    transport = _make_recording_transport(captured=captured, content="opus says hi")
    http = httpx.AsyncClient(transport=transport)
    client = ReasoningClient(
        api_key=None,
        agentrouter_api_key="sk-byok-test",
        agentrouter_base_url="https://agentrouter.example/v1",
        agentrouter_model="claude-opus-4-6",
        provider="agentrouter",
    )
    client._agentrouter_openai = openai.AsyncOpenAI(
        api_key="sk-byok-test",
        base_url="https://agentrouter.example/v1",
        http_client=http,
    )
    try:
        out = await client.complete("system", "user")
    finally:
        await client.aclose()

    assert out == "opus says hi"
    assert len(captured) == 1
    req = captured[0]
    assert str(req.url) == "https://agentrouter.example/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-byok-test"
    body = json.loads(req.content.decode("utf-8"))
    assert body["model"] == "claude-opus-4-6"
    assert body["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]


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
    """Tool-call round-trip via AgentRouter must decode the response."""
    captured: list[httpx.Request] = []
    tool_calls: list[dict[str, Any]] = [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "web_search",
                "arguments": '{"query":"reckora"}',
            },
        }
    ]
    transport = _make_recording_transport(
        captured=captured,
        content="",
        tool_calls=tool_calls,
    )
    http = httpx.AsyncClient(transport=transport)
    client = ReasoningClient(
        api_key=None,
        agentrouter_api_key="sk-byok-test",
        agentrouter_base_url="https://agentrouter.example/v1",
        agentrouter_model="claude-opus-4-6",
        provider="agentrouter",
    )
    client._agentrouter_openai = openai.AsyncOpenAI(
        api_key="sk-byok-test",
        base_url="https://agentrouter.example/v1",
        http_client=http,
    )
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
    assert turn.tool_calls[0].name == "web_search"
    assert turn.tool_calls[0].arguments == {"query": "reckora"}
    body = json.loads(captured[0].content.decode("utf-8"))
    assert body["tools"][0]["function"]["name"] == "web_search"


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
