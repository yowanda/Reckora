"""Tests for the ChatGPT OAuth function-calling path.

Covers:

* The chat-completions ``messages`` ↔ Responses ``input`` translator
  (system prompt is hoisted to ``instructions``, user/assistant
  messages keep their ordering, ``assistant.tool_calls`` become
  ``function_call`` items, ``role: tool`` becomes
  ``function_call_output``).
* The chat-completions ``tools`` ↔ Responses ``tools`` flattener.
* The codex SSE parser yields :class:`CodexFunctionCall` items from
  ``response.output_item.done`` events and falls back to the
  ``response.completed`` payload otherwise.
* :meth:`ReasoningClient.chat_with_tools` dispatches to the OAuth
  helper when no API key is configured but credentials are loaded,
  translates the result back into an :class:`AssistantTurn`, and
  raises :class:`ToolsNotSupportedError` when neither auth path is
  configured.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from reckora.auth.codex_client import (
    CodexFunctionCall,
    CodexResponsesTurn,
    complete_with_tools_via_codex,
)
from reckora.auth.oauth import OAuthCredentials
from reckora.reasoning.client import (
    AssistantToolCall,
    ReasoningClient,
    ToolsNotSupportedError,
    _function_call_to_assistant_tool_call,
    _messages_to_responses_input,
    _responses_turn_to_assistant_turn,
    _tools_to_responses_tools,
)


def _fresh_credentials() -> OAuthCredentials:
    """Build credentials that are not yet expired so refresh isn't triggered."""
    return OAuthCredentials(
        access_token="access-token",
        refresh_token="refresh-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        id_token=None,
    )


def test_messages_to_responses_input_hoists_system_and_user() -> None:
    """System prompts collapse into ``instructions``; user goes to ``input``."""
    instructions, items = _messages_to_responses_input(
        [
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "what is 2+2"},
        ]
    )
    assert instructions == "Be terse."
    assert items == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "what is 2+2"}],
        }
    ]


def test_messages_to_responses_input_concatenates_multiple_system_messages() -> None:
    """Repeated system messages join with blank lines so order is preserved."""
    instructions, _ = _messages_to_responses_input(
        [
            {"role": "system", "content": "rule 1"},
            {"role": "system", "content": "rule 2"},
            {"role": "user", "content": "go"},
        ]
    )
    assert instructions == "rule 1\n\nrule 2"


def test_messages_to_responses_input_emits_function_calls_and_outputs() -> None:
    """Assistant ``tool_calls`` and ``role: tool`` translate to Responses items."""
    _, items = _messages_to_responses_input(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "lookup foo"},
            {
                "role": "assistant",
                "content": "calling tool",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "arguments": '{"query":"foo"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": '{"results":[]}',
            },
        ]
    )
    assert items == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "lookup foo"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "calling tool"}],
        },
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "web_search",
            "arguments": '{"query":"foo"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"results":[]}',
        },
    ]


def test_tools_to_responses_tools_flattens_function_wrapper() -> None:
    """Chat-completions ``{type, function: {...}}`` flattens to Responses shape."""
    flattened = _tools_to_responses_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "search",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
    )
    assert flattened == [
        {
            "type": "function",
            "name": "web_search",
            "description": "search",
            "parameters": {"type": "object", "properties": {}},
        }
    ]


def test_function_call_to_assistant_tool_call_decodes_arguments() -> None:
    """The Responses arguments string is JSON-decoded for the agent layer."""
    decoded = _function_call_to_assistant_tool_call(
        CodexFunctionCall(call_id="c1", name="fetch_url", arguments='{"url":"https://x"}')
    )
    assert decoded == AssistantToolCall(id="c1", name="fetch_url", arguments={"url": "https://x"})


def test_function_call_to_assistant_tool_call_tolerates_bad_json() -> None:
    """A malformed arguments string degrades to an empty dict rather than raising."""
    decoded = _function_call_to_assistant_tool_call(
        CodexFunctionCall(call_id="c2", name="x", arguments="not json")
    )
    assert decoded == AssistantToolCall(id="c2", name="x", arguments={})


def test_responses_turn_to_assistant_turn_round_trips() -> None:
    """The combined turn translator preserves text + decodes function calls."""
    turn = _responses_turn_to_assistant_turn(
        CodexResponsesTurn(
            content="hello",
            function_calls=(
                CodexFunctionCall(call_id="c1", name="web_search", arguments='{"q":"x"}'),
            ),
        )
    )
    assert turn.content == "hello"
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "web_search"
    assert turn.tool_calls[0].arguments == {"q": "x"}


@pytest.mark.asyncio
async def test_complete_with_tools_via_codex_parses_function_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``output_item.done`` events with ``function_call`` items surface as
    :class:`CodexFunctionCall` entries on the returned turn."""
    captured_request: dict[str, Any] = {}

    sse_events = [
        'data: {"type":"response.output_item.done",'
        '"item":{"type":"function_call","call_id":"call_1",'
        '"name":"web_search","arguments":"{\\"query\\":\\"foo\\"}"}}',
        'data: {"type":"response.completed","response":{"output":[]}}',
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured_request.update(
            method=request.method,
            url=str(request.url),
            body=json.loads(request.content),
            authorization=request.headers.get("authorization"),
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="\n\n".join(sse_events).encode("utf-8") + b"\n\n",
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        turn = await complete_with_tools_via_codex(
            model="gpt-5.5",
            instructions="be terse",
            input_items=[
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "search foo"}],
                }
            ],
            tools=[
                {
                    "type": "function",
                    "name": "web_search",
                    "description": "search the web",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }
            ],
            access_token="access-token",
            client=client,
        )

    assert turn.content == ""
    assert len(turn.function_calls) == 1
    call = turn.function_calls[0]
    assert call.call_id == "call_1"
    assert call.name == "web_search"
    assert call.arguments == '{"query":"foo"}'

    # Wire-shape assertions: the Codex backend rejects requests that
    # don't match this body shape, so guard against accidental drift.
    body = captured_request["body"]
    assert body["model"] == "gpt-5.5"
    assert body["instructions"] == "be terse"
    assert body["stream"] is True
    assert body["store"] is False
    assert body["tool_choice"] == "auto"
    assert body["parallel_tool_calls"] is False
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["name"] == "web_search"
    assert "function" not in body["tools"][0]
    assert captured_request["authorization"] == "Bearer access-token"


@pytest.mark.asyncio
async def test_complete_with_tools_via_codex_falls_back_to_completed_payload() -> None:
    """When ``output_item.done`` is not emitted, calls come from ``response.completed``."""
    sse_events = [
        'data: {"type":"response.completed","response":{"output":[{'
        '"type":"function_call","call_id":"c2","name":"fetch_url",'
        '"arguments":"{\\"url\\":\\"https://example.com\\"}"}]}}',
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="\n\n".join(sse_events).encode("utf-8") + b"\n\n",
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        turn = await complete_with_tools_via_codex(
            model="gpt-5.5",
            instructions="",
            input_items=[],
            tools=[],
            access_token="t",
            client=client,
        )
    assert len(turn.function_calls) == 1
    assert turn.function_calls[0].name == "fetch_url"
    assert turn.function_calls[0].arguments == '{"url":"https://example.com"}'


@pytest.mark.asyncio
async def test_chat_with_tools_routes_to_oauth_when_api_key_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No API key + valid credentials → OAuth path executes function calling."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    captured_kwargs: dict[str, Any] = {}

    async def fake_codex(
        *,
        model: str,
        instructions: str,
        input_items: Any,
        tools: Any,
        access_token: str,
        client: httpx.AsyncClient,
        base_url: str = "",
        tool_choice: str = "auto",
        parallel_tool_calls: bool = False,
    ) -> CodexResponsesTurn:
        captured_kwargs.update(
            model=model,
            instructions=instructions,
            input_items=list(input_items),
            tools=list(tools),
            access_token=access_token,
            tool_choice=tool_choice,
        )
        return CodexResponsesTurn(
            content="",
            function_calls=(
                CodexFunctionCall(
                    call_id="oauth_call",
                    name="web_search",
                    arguments='{"query":"reckora"}',
                ),
            ),
        )

    monkeypatch.setattr("reckora.reasoning.client.complete_with_tools_via_codex", fake_codex)

    client = ReasoningClient(
        api_key=None,
        oauth_credentials=_fresh_credentials(),
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
                        "description": "search",
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
    # The OAuth path saw the translated wire shape.
    assert captured_kwargs["instructions"] == "be terse"
    assert captured_kwargs["tools"][0]["type"] == "function"
    assert captured_kwargs["tools"][0]["name"] == "web_search"
    assert "function" not in captured_kwargs["tools"][0]


@pytest.mark.asyncio
async def test_chat_with_tools_raises_when_no_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """No API key and no OAuth login → :class:`ToolsNotSupportedError`."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Point credentials_path at a location that doesn't exist so
    # load_credentials returns None.
    client = ReasoningClient(
        api_key=None,
        credentials_path=tmp_path / "nonexistent" / "auth.json",
    )
    try:
        with pytest.raises(ToolsNotSupportedError):
            await client.chat_with_tools(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
            )
    finally:
        await client.aclose()
