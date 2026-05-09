"""Async wrapper around the OpenAI / ChatGPT Codex chat APIs.

Reckora supports two auth modes through the same ``complete`` interface:

* **API key** (``OPENAI_API_KEY``, ``sk-...``): hits
  ``api.openai.com/v1/chat/completions`` via the ``openai`` SDK.
  Default behaviour and untouched from earlier releases.
* **ChatGPT OAuth** (login via ``reckora auth login``, persisted in
  ``~/.config/reckora/auth.json``): hits
  ``chatgpt.com/backend-api/codex/responses`` via a hand-rolled httpx
  streaming client. Lets a ChatGPT Plus / Pro subscriber drive the
  reasoning layer without provisioning a separate Platform billing
  account.

Resolution order at ``complete`` time:

1. Explicit ``oauth_credentials=`` constructor arg (tests / library
   embedders pinning a specific token).
2. Explicit ``api_key=`` constructor arg or ``OPENAI_API_KEY`` env
   var. This wins when set so existing API-key-based deploys keep
   their exact previous behaviour.
3. Credentials on disk at ``credentials_path`` (default
   ``~/.config/reckora/auth.json``) — the result of a successful
   ``reckora auth login``.
4. Otherwise: ``RuntimeError`` with a message that points the user
   at the two ways to authenticate.

OAuth mode auto-refreshes once on 401: if the access token has
expired (or has been revoked), we trade the long-lived refresh token
for fresh credentials, persist them back to ``credentials_path``,
and retry the request once. A subsequent 401 is fatal and surfaces
upstream so the user can re-login.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI

from ..auth.codex_client import (
    CodexFunctionCall,
    CodexResponsesTurn,
    complete_via_codex,
    complete_with_tools_via_codex,
)
from ..auth.oauth import OAuthCredentials, refresh_credentials
from ..auth.storage import load_credentials, save_credentials


class ToolsNotSupportedError(RuntimeError):
    """Raised when the current process has no usable LLM credentials at all.

    Both the chat-completions (API-key) and the Responses (OAuth)
    paths support tool calling natively, so this exception is now
    only used when neither auth path is configured. Kept as a
    distinct type so older callers can still ``except`` on it
    without a behaviour change.
    """


@dataclass(frozen=True)
class AssistantToolCall:
    """One ``tool_calls`` entry returned by the chat-completions API.

    ``arguments`` is already JSON-decoded — the upstream API sends it
    as a string. Decoding once at the boundary keeps the rest of the
    agent code working with plain dicts.
    """

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AssistantTurn:
    """A single assistant message, possibly carrying tool calls.

    Either ``content`` is a non-empty string and ``tool_calls`` is
    empty (the model is done) or ``tool_calls`` is non-empty and the
    caller is expected to execute them and feed the results back.
    """

    content: str
    tool_calls: tuple[AssistantToolCall, ...] = ()


class ReasoningClient:
    """Auth-aware async chat client. Dispatches between API-key and
    ChatGPT OAuth modes lazily based on what's configured."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        *,
        temperature: float = 0.2,
        oauth_credentials: OAuthCredentials | None = None,
        oauth_model: str = "gpt-5.5",
        credentials_path: Path | None = None,
    ) -> None:
        # Resolve API-key candidate eagerly so callers passing
        # ``api_key=None`` still see the env var (preserves the
        # historical contract).
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._model = model
        self._temperature = temperature
        self._explicit_oauth_credentials = oauth_credentials
        self._oauth_credentials = oauth_credentials
        self._oauth_model = oauth_model
        self._credentials_path = credentials_path
        # Lazy-instantiated; one OpenAI SDK client + one shared
        # AsyncClient for the OAuth path so we don't pay the TLS
        # handshake on every ``complete`` call.
        self._openai: AsyncOpenAI | None = None
        self._http: httpx.AsyncClient | None = None

    @property
    def model(self) -> str:
        """The model name used by the API-key mode."""
        return self._model

    @property
    def oauth_model(self) -> str:
        """The model name used by the ChatGPT OAuth mode."""
        return self._oauth_model

    async def complete(self, system: str, user: str) -> str:
        """Run a single completion and return the assistant text.

        Picks API-key vs OAuth lazily so a process that has both
        credentials available (e.g. CI with ``OPENAI_API_KEY`` set
        on a developer laptop that also has a ``~/.config/reckora``
        login from a different project) deterministically prefers
        the API key.
        """
        if self._api_key:
            return await self._complete_via_api_key(system, user)
        creds = self._oauth_credentials or load_credentials(path=self._credentials_path)
        if creds is None:
            raise RuntimeError(
                "no OpenAI credentials available — set OPENAI_API_KEY or run `reckora auth login`"
            )
        return await self._complete_via_oauth(creds, system, user)

    async def _complete_via_api_key(self, system: str, user: str) -> str:
        client = self._get_openai_client()
        resp = await client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self._temperature,
        )
        return resp.choices[0].message.content or ""

    async def chat_with_tools(
        self,
        *,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        tool_choice: str = "auto",
    ) -> AssistantTurn:
        """Single-turn chat completion with tool calling enabled.

        Caller manages the conversation array — appending the assistant
        message, tool result messages, and re-calling this method
        until the model returns plain content. The conversation /
        tool format is the chat-completions wire shape; the OAuth
        path translates it on the fly to the Responses-API shape
        (which is what ``chatgpt.com/backend-api/codex/responses``
        natively speaks).

        Dispatch order matches :meth:`complete`: explicit
        ``OPENAI_API_KEY`` wins, otherwise ChatGPT OAuth, otherwise
        :class:`ToolsNotSupportedError` (no usable credentials).
        """
        if self._api_key:
            return await self._chat_with_tools_via_api_key(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
            )
        creds = self._oauth_credentials or load_credentials(path=self._credentials_path)
        if creds is None:
            raise ToolsNotSupportedError(
                "tool-using AgentLoop requires either OPENAI_API_KEY or a "
                "successful `reckora auth login`"
            )
        return await self._chat_with_tools_via_oauth(
            credentials=creds,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )

    async def _chat_with_tools_via_api_key(
        self,
        *,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        tool_choice: str,
    ) -> AssistantTurn:
        client = self._get_openai_client()
        # The OpenAI SDK's ``messages``/``tools`` parameters are typed
        # as a Union of TypedDicts; we hand-roll dicts here because the
        # tool-call message shape only needs runtime correctness, not a
        # static commitment to one of the TypedDict variants.
        resp = await client.chat.completions.create(  # type: ignore[call-overload]
            model=self._model,
            messages=list(messages),
            tools=list(tools),
            tool_choice=tool_choice,
            temperature=self._temperature,
        )
        choice = resp.choices[0].message
        raw_calls = getattr(choice, "tool_calls", None) or []
        decoded_calls: list[AssistantToolCall] = []
        for call in raw_calls:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            decoded_calls.append(
                AssistantToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=arguments,
                )
            )
        return AssistantTurn(
            content=choice.content or "",
            tool_calls=tuple(decoded_calls),
        )

    async def _chat_with_tools_via_oauth(
        self,
        *,
        credentials: OAuthCredentials,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        tool_choice: str,
    ) -> AssistantTurn:
        """Drive function calling through the ChatGPT Codex Responses API.

        Translates the chat-completions ``messages`` array to the
        Responses ``input`` array (with the system prompt hoisted to
        ``instructions``) and the chat-completions ``tools`` shape to
        the flat Responses ``tools`` shape, calls the codex helper,
        and translates the response back into an
        :class:`AssistantTurn` so the caller is unaware of the
        backend dispatch.
        """
        instructions, input_items = _messages_to_responses_input(messages)
        responses_tools = _tools_to_responses_tools(tools)
        if credentials.is_expired():
            credentials = await self._refresh_and_persist(credentials)
        http = self._get_http_client()
        try:
            turn = await complete_with_tools_via_codex(
                model=self._oauth_model,
                instructions=instructions,
                input_items=input_items,
                tools=responses_tools,
                tool_choice=tool_choice,
                access_token=credentials.access_token,
                client=http,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
            credentials = await self._refresh_and_persist(credentials)
            turn = await complete_with_tools_via_codex(
                model=self._oauth_model,
                instructions=instructions,
                input_items=input_items,
                tools=responses_tools,
                tool_choice=tool_choice,
                access_token=credentials.access_token,
                client=http,
            )
        return _responses_turn_to_assistant_turn(turn)

    async def _complete_via_oauth(
        self,
        credentials: OAuthCredentials,
        system: str,
        user: str,
    ) -> str:
        # Eagerly refresh if the access token is within the skew
        # window of expiry. This avoids the cost of a guaranteed-401
        # round-trip when we already know the token's stale.
        if credentials.is_expired():
            credentials = await self._refresh_and_persist(credentials)

        http = self._get_http_client()
        try:
            return await complete_via_codex(
                model=self._oauth_model,
                system=system,
                user=user,
                access_token=credentials.access_token,
                client=http,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
            # Refresh once and retry; any subsequent 401 is fatal.
            credentials = await self._refresh_and_persist(credentials)
            return await complete_via_codex(
                model=self._oauth_model,
                system=system,
                user=user,
                access_token=credentials.access_token,
                client=http,
            )

    async def _refresh_and_persist(
        self,
        credentials: OAuthCredentials,
    ) -> OAuthCredentials:
        """Trade the refresh token for new credentials and persist
        them back to disk so future processes pick them up.

        Updates ``self._oauth_credentials`` so subsequent calls in
        the same process don't re-load from disk.
        """
        http = self._get_http_client()
        refreshed = await refresh_credentials(credentials.refresh_token, client=http)
        # Only write to disk when we sourced the original credentials
        # from disk — embedders that supplied an explicit
        # ``oauth_credentials`` kwarg manage persistence themselves.
        if self._explicit_oauth_credentials is None:
            save_credentials(refreshed, path=self._credentials_path)
        self._oauth_credentials = refreshed
        return refreshed

    def _get_openai_client(self) -> AsyncOpenAI:
        if self._api_key is None:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        if self._openai is None:
            self._openai = AsyncOpenAI(api_key=self._api_key)
        return self._openai

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        return self._http

    async def aclose(self) -> None:
        """Release pooled HTTP resources.

        Optional — Python's GC closes both clients on collection, but
        long-lived ``ReasoningClient`` instances inside async tests
        leak warnings without an explicit close.
        """
        http = self._http
        if http is not None:
            await http.aclose()
            self._http = None
        openai_client = self._openai
        if openai_client is not None:
            await openai_client.close()
            self._openai = None


def _messages_to_responses_input(
    messages: Sequence[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Translate chat-completions messages to Responses-API ``input``.

    Returns a tuple of ``(instructions, input_items)``:

    * ``instructions`` — the concatenated content of every ``system``
      message. Responses-API hoists the system prompt out of the
      conversation array.
    * ``input_items`` — every non-system message, mapped to the
      Responses item shape:

      - ``role: user|assistant`` with string content → a ``message``
        item carrying ``input_text`` / ``output_text`` content
        items.
      - ``role: assistant`` with ``tool_calls`` → one
        ``function_call`` item per tool call (the assistant's text,
        if any, is preserved as a sibling ``message`` item).
      - ``role: tool`` with ``tool_call_id`` → a
        ``function_call_output`` item.
    """
    instructions_parts: list[str] = []
    input_items: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "system":
            if isinstance(content, str) and content:
                instructions_parts.append(content)
            continue
        if role == "tool":
            call_id = msg.get("tool_call_id")
            if not isinstance(call_id, str):
                continue
            output_text = content if isinstance(content, str) else json.dumps(content)
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output_text,
                }
            )
            continue
        if role == "assistant":
            if isinstance(content, str) and content:
                input_items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": content}],
                    }
                )
            tool_calls = msg.get("tool_calls") or []
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or {}
                name = function.get("name")
                arguments = function.get("arguments")
                call_id = call.get("id")
                if not isinstance(name, str) or not isinstance(call_id, str):
                    continue
                if isinstance(arguments, dict):
                    arguments = json.dumps(arguments)
                if not isinstance(arguments, str):
                    arguments = "{}"
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": arguments,
                    }
                )
            continue
        if role == "user":
            text = content if isinstance(content, str) else json.dumps(content)
            input_items.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                }
            )
            continue
    return "\n\n".join(instructions_parts), input_items


def _tools_to_responses_tools(
    tools: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flatten chat-completions ``{type: function, function: {...}}`` tools.

    The Responses API expects the function fields at the top level of
    each tool entry (``type``, ``name``, ``description``,
    ``parameters``), not nested under a ``function`` key.
    """
    out: list[dict[str, Any]] = []
    for spec in tools:
        if spec.get("type") != "function":
            # Non-function tools (e.g. built-in web search) are
            # already in the Responses shape — pass through.
            out.append(dict(spec))
            continue
        fn = spec.get("function") or {}
        flattened = {
            "type": "function",
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters") or {},
        }
        out.append(flattened)
    return out


def _responses_turn_to_assistant_turn(turn: CodexResponsesTurn) -> AssistantTurn:
    """Translate a Codex Responses turn into the chat-completions shape."""
    decoded_calls: list[AssistantToolCall] = []
    for call in turn.function_calls:
        decoded_calls.append(_function_call_to_assistant_tool_call(call))
    return AssistantTurn(content=turn.content, tool_calls=tuple(decoded_calls))


def _function_call_to_assistant_tool_call(call: CodexFunctionCall) -> AssistantToolCall:
    """Decode a Codex function call into the agent's chat-completions shape."""
    try:
        arguments = json.loads(call.arguments) if call.arguments else {}
    except (json.JSONDecodeError, TypeError):
        arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    return AssistantToolCall(
        id=call.call_id,
        name=call.name,
        arguments=arguments,
    )
