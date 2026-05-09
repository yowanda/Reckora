"""Thin async client for the ChatGPT Codex Responses API.

Two entry points:

* ``complete_via_codex`` — single text completion, returns the
  concatenated assistant text. Used by the legacy passive AI path
  (summarize / hypothesize) and by the AgentLoop's planner step.
* ``complete_with_tools_via_codex`` — multi-turn tool-call helper.
  Accepts the full Responses-API ``input`` array (so the caller can
  carry prior ``function_call`` / ``function_call_output`` items
  forward across turns) and a list of function-tool definitions.
  Returns both the assistant text *and* any ``function_call`` items
  the model emitted, so the caller can dispatch them and feed the
  results back on the next turn.

Wire shape (via streaming SSE):

* ``response.output_text.delta`` events accumulate assistant text.
* ``response.output_item.done`` events with ``item.type=function_call``
  surface a tool call (``call_id``, ``name``, ``arguments`` is a
  JSON-encoded string).
* ``response.completed`` is the final event; we use it as a fallback
  for assistants that batch the full text instead of streaming
  deltas.

This mirrors the wire format documented at
https://platform.openai.com/docs/guides/function-calling?api-mode=responses
and the open-source ``codex-rs`` client.

Why a hand-rolled httpx client instead of the ``openai`` SDK? The
Codex backend (``chatgpt.com/backend-api/codex/responses``) *requires*
streaming responses — non-streaming requests are rejected. That
requirement, plus a couple of small payload-shape divergences from the
upstream Responses API, mean we'd be monkey-patching the SDK to make
it work (which is exactly what ``codex-auth`` on PyPI does). A
hand-rolled httpx wrapper is lighter, keeps the divergences localised,
and makes the request shape *testable* end-to-end with ``pytest-httpx``.
The reasoning layer treats this as a black box that maps
``(system, user) → assistant text`` (or, for tool calls, a stream of
function calls + final text) just like the API-key code path.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from .oauth import CHATGPT_CODEX_BASE_URL


@dataclass(frozen=True)
class CodexFunctionCall:
    """A single ``function_call`` item returned by the Responses API.

    ``arguments`` is the raw JSON-encoded string the model emitted —
    callers parse it themselves (matches both the chat-completions
    ``function.arguments`` and Responses ``function_call.arguments``
    contracts; both are strings).
    """

    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class CodexResponsesTurn:
    """One assistant turn from the Codex Responses API.

    Either the model returned plain text (``content`` populated,
    ``function_calls`` empty) or it requested one or more function
    calls (``function_calls`` populated; ``content`` may also have
    a "thinking-out-loud" preface).
    """

    content: str
    function_calls: tuple[CodexFunctionCall, ...] = field(default_factory=tuple)


# Body schema for the Codex Responses API. Matches what the OpenAI
# Codex CLI sends, observed via the ``openai-oauth`` npm package and
# the ``codex-auth`` PyPI source.
#
# - ``input`` is an array of role-tagged messages whose ``content``
#   items are typed (``input_text`` for the user prompt). System
#   prompts go into the top-level ``instructions`` field rather than
#   a ``system`` role — that's the Responses-API convention.
# - ``stream`` MUST be true; the Codex backend rejects non-streaming
#   requests with a 4xx.


async def complete_via_codex(
    *,
    model: str,
    system: str,
    user: str,
    access_token: str,
    client: httpx.AsyncClient,
    base_url: str = CHATGPT_CODEX_BASE_URL,
) -> str:
    """Run a single Responses-API call and return the concatenated
    assistant text.

    Streams the response, accumulates ``output_text.delta`` events,
    and falls back to digging through the final ``response.completed``
    payload if the upstream chose not to stream deltas (some Codex
    models batch the full text into the completion event).
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    body = {
        "model": model,
        "instructions": system,
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user}],
            }
        ],
        "stream": True,
        # The Codex backend rejects ChatGPT-account requests that
        # leave ``store`` unset (or set to true) with a
        # ``400 {"detail":"Store must be set to false"}``. The Codex
        # CLI itself sends this constant, mirroring the privacy
        # contract that Codex ChatGPT-account traffic is never
        # written into the user's response history.
        "store": False,
    }

    chunks: list[str] = []
    completed_text: str | None = None
    async with client.stream(
        "POST",
        f"{base_url}/responses",
        json=body,
        headers=headers,
    ) as resp:
        resp.raise_for_status()
        async for event in _iter_sse_events(resp.aiter_lines()):
            event_type = event.get("type")
            if event_type == "response.output_text.delta":
                delta = event.get("delta")
                if isinstance(delta, str):
                    chunks.append(delta)
            elif event_type == "response.completed":
                completed_text = _extract_completed_text(event)
            elif event_type == "error":
                # Server-side error mid-stream; surface it as a hard
                # failure so the caller can refresh credentials and
                # retry rather than silently returning a partial.
                msg = event.get("message") or event.get("error") or "stream error"
                raise CodexStreamError(str(msg))

    if chunks:
        return "".join(chunks)
    if completed_text is not None:
        return completed_text
    return ""


async def complete_with_tools_via_codex(
    *,
    model: str,
    instructions: str,
    input_items: Sequence[dict[str, Any]],
    tools: Sequence[dict[str, Any]],
    access_token: str,
    client: httpx.AsyncClient,
    base_url: str = CHATGPT_CODEX_BASE_URL,
    tool_choice: str = "auto",
    parallel_tool_calls: bool = False,
) -> CodexResponsesTurn:
    """Run a single Responses-API call with function tools.

    The caller is responsible for managing the conversation: appending
    its own ``message`` items as the user, the previous turn's
    ``function_call`` items, and ``function_call_output`` items
    carrying the tool results, then re-calling this helper. The shape
    of ``input_items`` and ``tools`` is the Responses-API native
    format (no chat-completions ``function`` wrapper).

    Returns a :class:`CodexResponsesTurn` containing whatever
    text + function calls the model emitted. The helper does *not*
    loop — that's the orchestrator's job upstream so it can enforce
    a budget and materialise traces between turns.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    body: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": list(input_items),
        "tools": list(tools),
        "tool_choice": tool_choice,
        "parallel_tool_calls": parallel_tool_calls,
        "stream": True,
        "store": False,
    }

    text_chunks: list[str] = []
    completed_text: str | None = None
    function_calls: list[CodexFunctionCall] = []
    completed_calls: list[CodexFunctionCall] = []
    async with client.stream(
        "POST",
        f"{base_url}/responses",
        json=body,
        headers=headers,
    ) as resp:
        resp.raise_for_status()
        async for event in _iter_sse_events(resp.aiter_lines()):
            event_type = event.get("type")
            if event_type == "response.output_text.delta":
                delta = event.get("delta")
                if isinstance(delta, str):
                    text_chunks.append(delta)
            elif event_type == "response.output_item.done":
                call = _extract_function_call_item(event)
                if call is not None:
                    function_calls.append(call)
            elif event_type == "response.completed":
                completed_text = _extract_completed_text(event)
                completed_calls = _extract_completed_function_calls(event)
            elif event_type == "error":
                msg = event.get("message") or event.get("error") or "stream error"
                raise CodexStreamError(str(msg))

    # Prefer per-item ``output_item.done`` signals (they fire as soon
    # as each call is finalised) but fall back to the ``completed``
    # event's full output array for servers that don't emit them.
    final_calls = function_calls or completed_calls
    text = "".join(text_chunks) or completed_text or ""
    return CodexResponsesTurn(
        content=text,
        function_calls=tuple(final_calls),
    )


class CodexStreamError(RuntimeError):
    """Raised when the Codex Responses stream emits an ``error`` event.

    Distinct from ``httpx.HTTPStatusError`` (which covers transport-
    level 4xx/5xx) so callers can differentiate "credentials are
    bad, refresh + retry" from "the model produced an error event in
    the middle of a 200 stream".
    """


async def _iter_sse_events(
    lines: AsyncIterator[str],
) -> AsyncIterator[dict[str, object]]:
    """Yield decoded JSON event objects from an SSE byte stream.

    Server-Sent Events come as ``data: {...json...}`` lines separated
    by blank lines. We tolerate ``[DONE]`` sentinels (which carry no
    JSON), unknown ``event:`` lines (we only consume ``data:``), and
    multi-line ``data:`` payloads spanning several lines per spec
    (concatenated with newlines).
    """
    buffer: list[str] = []
    async for raw in lines:
        # ``aiter_lines`` already strips the trailing ``\n``.
        line = raw.rstrip("\r")
        if not line:
            # Blank line == event boundary. Flush the buffer.
            if buffer:
                payload = "\n".join(buffer)
                buffer = []
                if payload and payload != "[DONE]":
                    try:
                        decoded = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(decoded, dict):
                        yield decoded
            continue
        if line.startswith("data:"):
            buffer.append(line[len("data:") :].lstrip())
        # ``event:`` / ``id:`` / comment lines are ignored.

    # End-of-stream flush in case the server forgot the final blank
    # line (some proxies strip it).
    if buffer:
        payload = "\n".join(buffer)
        if payload and payload != "[DONE]":
            try:
                decoded = json.loads(payload)
            except json.JSONDecodeError:
                return
            if isinstance(decoded, dict):
                yield decoded


def _extract_completed_text(event: dict[str, object]) -> str | None:
    """Pull the assistant text out of a ``response.completed`` event.

    The completed event nests:
      ``event.response.output[].content[].text``
    (Only ``output_text`` content items contribute.) Returns ``None``
    if no text fields were present so callers know to fall back.
    """
    response = event.get("response")
    if not isinstance(response, dict):
        return None
    output = response.get("output")
    if not isinstance(output, list):
        return None
    pieces: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for content_item in content:
            if not isinstance(content_item, dict):
                continue
            if content_item.get("type") == "output_text":
                text = content_item.get("text")
                if isinstance(text, str):
                    pieces.append(text)
    return "".join(pieces) if pieces else None


def _extract_function_call_item(event: dict[str, object]) -> CodexFunctionCall | None:
    """Return the ``function_call`` payload inside a ``response.output_item.done``.

    The Responses API emits ``output_item.done`` once per finalised
    output item. For function calls, the item shape is:

      {"type": "function_call", "call_id": "...", "name": "...",
       "arguments": "{json-string}"}
    """
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    if item.get("type") != "function_call":
        return None
    call_id = item.get("call_id")
    name = item.get("name")
    arguments = item.get("arguments")
    if not isinstance(call_id, str) or not isinstance(name, str):
        return None
    if not isinstance(arguments, str):
        return None
    return CodexFunctionCall(call_id=call_id, name=name, arguments=arguments)


def _extract_completed_function_calls(
    event: dict[str, object],
) -> list[CodexFunctionCall]:
    """Pull every ``function_call`` item out of a ``response.completed`` event.

    Used as a fallback when the upstream batches output items into the
    final completion payload instead of emitting per-item ``done``
    events.
    """
    response = event.get("response")
    if not isinstance(response, dict):
        return []
    output = response.get("output")
    if not isinstance(output, list):
        return []
    calls: list[CodexFunctionCall] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "function_call":
            continue
        call_id = item.get("call_id")
        name = item.get("name")
        arguments = item.get("arguments")
        if isinstance(call_id, str) and isinstance(name, str) and isinstance(arguments, str):
            calls.append(CodexFunctionCall(call_id=call_id, name=name, arguments=arguments))
    return calls
