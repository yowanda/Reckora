"""Thin async client for the ChatGPT Codex Responses API.

Why a hand-rolled httpx client instead of the ``openai`` SDK?

The Codex backend (``chatgpt.com/backend-api/codex/responses``)
*requires* streaming responses — non-streaming requests are rejected.
That requirement, plus a couple of small payload-shape divergences
from the upstream Responses API, mean we'd be monkey-patching the SDK
to make it work (which is exactly what ``codex-auth`` on PyPI does).

A 50-line httpx wrapper is lighter, keeps the divergences localised
and — importantly — makes the request shape *testable* end-to-end
with ``pytest-httpx``. The reasoning layer treats this as a black box
that maps ``(system, user) → assistant text`` just like the
API-key code path.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from .oauth import CHATGPT_CODEX_BASE_URL

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
