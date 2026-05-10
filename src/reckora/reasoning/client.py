"""Async wrapper around the OpenAI / ChatGPT Codex / AgentRouter chat APIs.

Reckora supports three auth modes through the same ``complete`` interface:

* **API key** (``OPENAI_API_KEY``, ``sk-...``): hits
  ``api.openai.com/v1/chat/completions`` via the ``openai`` SDK.
  Default behaviour and untouched from earlier releases.
* **ChatGPT OAuth** (login via ``reckora auth login``, persisted in
  ``~/.config/reckora/auth.json``): hits
  ``chatgpt.com/backend-api/codex/responses`` via a hand-rolled httpx
  streaming client. Lets a ChatGPT Plus / Pro subscriber drive the
  reasoning layer without provisioning a separate Platform billing
  account.
* **AgentRouter** (``AGENTROUTER_API_KEY`` env var or per-user BYOK):
  hits ``agentrouter.org/v1/messages`` via the **Anthropic** SDK
  ``AsyncAnthropic(base_url="https://agentrouter.org/")``.
  AgentRouter is an LLM gateway that fronts Claude (and other
  models) but its WAF allowlists requests by client fingerprint
  (TLS + SDK headers). Generic OpenAI-SDK calls against
  ``/v1/chat/completions`` are rejected with
  ``unauthorized client detected``; only the officially-supported
  clients (Claude Code, Codex, Gemini CLI, RooCode, Kilocode,
  Qwen Code, Droid CLI) and the Anthropic SDKs they're built on
  get past the check. Hence: Anthropic SDK against the
  Anthropic-compatible ``/v1/messages`` route. The OpenAI-style
  ``messages`` / ``tools`` payloads we already produce internally
  are translated to the Anthropic shape on the boundary.

Resolution order at ``complete`` time:

0. Explicit ``provider="agentrouter" | "openai" | "chatgpt_oauth"``
   constructor arg pins one path and skips the auto chain. Used by
   the API layer when the request payload selects a provider.
1. Explicit ``oauth_credentials=`` constructor arg (tests / library
   embedders pinning a specific token).
2. Explicit ``api_key=`` constructor arg or ``OPENAI_API_KEY`` env
   var. This wins when set so existing API-key-based deploys keep
   their exact previous behaviour.
3. Credentials on disk at ``credentials_path`` (default
   ``~/.config/reckora/auth.json``) — the result of a successful
   ``reckora auth login``.
4. Otherwise: ``RuntimeError`` with a message that points the user
   at the three ways to authenticate.

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
from typing import Any, Literal

import httpx
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from ..auth.codex_client import (
    CodexFunctionCall,
    CodexResponsesTurn,
    complete_via_codex,
    complete_with_tools_via_codex,
)
from ..auth.oauth import OAuthCredentials, refresh_credentials
from ..auth.storage import load_credentials, save_credentials

ProviderName = Literal["auto", "openai", "chatgpt_oauth", "agentrouter"]

VALID_PROVIDERS: frozenset[str] = frozenset({"auto", "openai", "chatgpt_oauth", "agentrouter"})


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
        agentrouter_api_key: str | None = None,
        agentrouter_base_url: str = "https://agentrouter.org/",
        agentrouter_model: str = "claude-opus-4-6",
        agentrouter_max_tokens: int = 4096,
        provider: ProviderName = "auto",
    ) -> None:
        if provider not in VALID_PROVIDERS:
            raise ValueError(
                f"unknown provider {provider!r}; expected one of: "
                + ", ".join(sorted(VALID_PROVIDERS))
            )
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
        # AgentRouter (https://agentrouter.org) is fronted by an
        # Aliyun WAF that whitelists requests by client fingerprint.
        # The OpenAI Python SDK is *not* on that allowlist, but the
        # Anthropic SDK (which Claude Code is built on) is — so the
        # AgentRouter path uses ``AsyncAnthropic`` against the
        # Anthropic-compatible ``/v1/messages`` route instead of the
        # OpenAI-compatible ``/v1/chat/completions`` route. The
        # ``base_url`` therefore points at the AgentRouter root
        # (``https://agentrouter.org/``), not the ``/v1`` path.
        self._agentrouter_api_key = agentrouter_api_key or os.environ.get("AGENTROUTER_API_KEY")
        self._agentrouter_base_url = agentrouter_base_url
        self._agentrouter_model = agentrouter_model
        self._agentrouter_max_tokens = agentrouter_max_tokens
        self._provider: ProviderName = provider
        # Lazy-instantiated; one SDK client per backend + one shared
        # AsyncClient for the OAuth path so we don't pay the TLS
        # handshake on every ``complete`` call.
        self._openai: AsyncOpenAI | None = None
        self._agentrouter_anthropic: AsyncAnthropic | None = None
        self._http: httpx.AsyncClient | None = None

    @property
    def model(self) -> str:
        """The model name used by the API-key mode."""
        return self._model

    @property
    def oauth_model(self) -> str:
        """The model name used by the ChatGPT OAuth mode."""
        return self._oauth_model

    @property
    def agentrouter_model(self) -> str:
        """The model name used by the AgentRouter mode."""
        return self._agentrouter_model

    @property
    def provider(self) -> ProviderName:
        """Resolved provider preference. ``auto`` = lazy chain."""
        return self._provider

    async def complete(self, system: str, user: str) -> str:
        """Run a single completion and return the assistant text.

        Picks API-key vs OAuth vs AgentRouter lazily. When
        ``provider`` is ``"auto"`` (the default) the resolution
        order is API key > ChatGPT OAuth, with AgentRouter only
        used when the caller pins it explicitly. When ``provider``
        is anything else the corresponding path is required and a
        missing credential is a hard error.
        """
        if self._provider == "agentrouter":
            if not self._agentrouter_api_key:
                raise RuntimeError(
                    "provider='agentrouter' was requested but no AgentRouter "
                    "API key is configured (set AGENTROUTER_API_KEY or save "
                    "a per-user key on the user's settings)."
                )
            return await self._complete_via_agentrouter(system, user)
        if self._provider == "openai":
            if not self._api_key:
                raise RuntimeError("provider='openai' was requested but OPENAI_API_KEY is unset.")
            return await self._complete_via_api_key(system, user)
        if self._provider == "chatgpt_oauth":
            creds = self._oauth_credentials or load_credentials(path=self._credentials_path)
            if creds is None:
                raise RuntimeError(
                    "provider='chatgpt_oauth' was requested but no OAuth "
                    "credentials are present — run `reckora auth login`."
                )
            return await self._complete_via_oauth(creds, system, user)
        # provider == "auto" — historical chain.
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

    async def _complete_via_agentrouter(self, system: str, user: str) -> str:
        """Run a single completion through AgentRouter.

        Uses the Anthropic SDK against the Anthropic-compatible
        ``/v1/messages`` route; AgentRouter's WAF blocks the OpenAI
        SDK fingerprint regardless of credential. The system prompt
        is hoisted out of the messages array (Anthropic requires it
        as a separate parameter).
        """
        client = self._get_agentrouter_client()
        resp = await client.messages.create(
            model=self._agentrouter_model,
            system=system,
            max_tokens=self._agentrouter_max_tokens,
            temperature=self._temperature,
            messages=[{"role": "user", "content": user}],
        )
        return _anthropic_text_from_blocks(resp.content)

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

        Dispatch order matches :meth:`complete`: when ``provider``
        is pinned the corresponding path is required; under
        ``provider='auto'`` the resolution order is API key >
        ChatGPT OAuth, with AgentRouter only used when explicitly
        selected. Missing credentials raise
        :class:`ToolsNotSupportedError`.
        """
        if self._provider == "agentrouter":
            if not self._agentrouter_api_key:
                raise ToolsNotSupportedError(
                    "provider='agentrouter' was requested but no AgentRouter "
                    "API key is configured (set AGENTROUTER_API_KEY or save "
                    "a per-user key on the user's settings)."
                )
            return await self._chat_with_tools_via_agentrouter(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
            )
        if self._provider == "openai":
            if not self._api_key:
                raise ToolsNotSupportedError(
                    "provider='openai' was requested but OPENAI_API_KEY is unset."
                )
            return await self._chat_with_tools_via_api_key(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
            )
        if self._provider == "chatgpt_oauth":
            creds = self._oauth_credentials or load_credentials(path=self._credentials_path)
            if creds is None:
                raise ToolsNotSupportedError(
                    "provider='chatgpt_oauth' was requested but no OAuth "
                    "credentials are present — run `reckora auth login`."
                )
            return await self._chat_with_tools_via_oauth(
                credentials=creds,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
            )
        # provider == "auto" — historical chain.
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

    async def _chat_with_tools_via_agentrouter(
        self,
        *,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        tool_choice: str,
    ) -> AssistantTurn:
        """Tool-call round-trip via AgentRouter (Anthropic SDK path).

        Translates the OpenAI-style ``messages`` and ``tools`` we
        carry internally into the Anthropic ``/v1/messages`` shape
        (system param hoisted, ``tool`` results encoded as
        ``tool_result`` content blocks, ``function`` tools as
        ``input_schema`` definitions), then maps the Anthropic
        response back into our :class:`AssistantTurn` so callers
        stay backend-agnostic.
        """
        client = self._get_agentrouter_client()
        system, anthropic_messages = _messages_to_anthropic_messages(messages)
        anthropic_tools = _tools_to_anthropic_tools(tools)
        anthropic_tool_choice = _tool_choice_to_anthropic(tool_choice)
        # Anthropic typing accepts the union of message/tool TypedDicts;
        # we hand-roll dicts here because the runtime contract is what
        # matters for the WAF-allowlisted /v1/messages payload.
        resp = await client.messages.create(  # type: ignore[call-overload]
            model=self._agentrouter_model,
            system=system or "",
            max_tokens=self._agentrouter_max_tokens,
            temperature=self._temperature,
            messages=anthropic_messages,
            tools=anthropic_tools,
            tool_choice=anthropic_tool_choice,
        )
        return _anthropic_response_to_assistant_turn(resp)

    async def _chat_with_tools_via_api_key(
        self,
        *,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        tool_choice: str,
    ) -> AssistantTurn:
        return await self._chat_with_tools_using_openai_sdk(
            client=self._get_openai_client(),
            model=self._model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )

    async def _chat_with_tools_using_openai_sdk(
        self,
        *,
        client: AsyncOpenAI,
        model: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        tool_choice: str,
    ) -> AssistantTurn:
        """Shared implementation for OpenAI-SDK-based providers.

        Both the direct OpenAI API-key path and the AgentRouter
        gateway speak the OpenAI ``/v1/chat/completions`` shape and
        return the same ``ChoiceMessage`` payload, so encoding and
        decoding lives here — the per-provider methods only own
        client construction and the model name.
        """
        # The OpenAI SDK's ``messages``/``tools`` parameters are typed
        # as a Union of TypedDicts; we hand-roll dicts here because the
        # tool-call message shape only needs runtime correctness, not a
        # static commitment to one of the TypedDict variants.
        resp = await client.chat.completions.create(  # type: ignore[call-overload]
            model=model,
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

    def _get_agentrouter_client(self) -> AsyncAnthropic:
        if self._agentrouter_api_key is None:
            raise RuntimeError("AGENTROUTER_API_KEY is not configured")
        if self._agentrouter_anthropic is None:
            self._agentrouter_anthropic = AsyncAnthropic(
                api_key=self._agentrouter_api_key,
                base_url=self._agentrouter_base_url,
            )
        return self._agentrouter_anthropic

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
        agentrouter_client = self._agentrouter_anthropic
        if agentrouter_client is not None:
            await agentrouter_client.close()
            self._agentrouter_anthropic = None


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


def _messages_to_anthropic_messages(
    messages: Sequence[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Translate chat-completions messages to the Anthropic ``/v1/messages`` shape.

    The Anthropic Messages API differs from chat-completions in three ways:

    * The system prompt is a separate top-level ``system`` parameter,
      not a message with ``role=system``. Multiple system messages
      are concatenated with blank-line separators.
    * Tool *invocations* are encoded as ``tool_use`` content blocks
      on the assistant message, not as a sibling ``tool_calls``
      array.
    * Tool *results* are encoded as ``tool_result`` content blocks
      on a ``user`` message, not as a separate ``role=tool``
      message.

    Returns ``(system_prompt, anthropic_messages)``. The returned
    list always alternates assistant / user roles per Anthropic's
    requirements; the caller is responsible for ordering messages
    correctly upstream.
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "system":
            if isinstance(content, str) and content:
                system_parts.append(content)
            continue
        if role == "tool":
            call_id = msg.get("tool_call_id")
            if not isinstance(call_id, str):
                continue
            tool_text = content if isinstance(content, str) else json.dumps(content)
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": tool_text,
                        }
                    ],
                }
            )
            continue
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            if isinstance(content, str) and content:
                blocks.append({"type": "text", "text": content})
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") or {}
                call_id = call.get("id")
                if not isinstance(call_id, str):
                    continue
                raw_args = fn.get("arguments")
                try:
                    parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except (json.JSONDecodeError, TypeError):
                    parsed_args = {}
                if not isinstance(parsed_args, dict):
                    parsed_args = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": fn.get("name") or "",
                        "input": parsed_args,
                    }
                )
            if blocks:
                out.append({"role": "assistant", "content": blocks})
            continue
        if role == "user":
            if isinstance(content, str):
                out.append({"role": "user", "content": content})
            elif isinstance(content, list):
                # Pass structured content blocks through unchanged.
                out.append({"role": "user", "content": list(content)})
            continue
    return "\n\n".join(system_parts), out


def _tools_to_anthropic_tools(
    tools: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Translate chat-completions ``function`` tools to the Anthropic shape.

    Anthropic exposes ``{name, description, input_schema}`` directly
    at the top level of each tool entry; the ``type: function``
    wrapper and ``parameters`` key from chat-completions are
    discarded.
    """
    out: list[dict[str, Any]] = []
    for spec in tools:
        if spec.get("type") != "function":
            # Non-function tools (e.g. built-in providers) are
            # already in a top-level shape — pass through verbatim
            # so callers can opt into Anthropic-native tools.
            out.append(dict(spec))
            continue
        fn = spec.get("function") or {}
        out.append(
            {
                "name": fn.get("name") or "",
                "description": fn.get("description") or "",
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return out


def _tool_choice_to_anthropic(tool_choice: str) -> dict[str, Any]:
    """Translate the chat-completions ``tool_choice`` string into Anthropic's object shape."""
    if tool_choice == "auto":
        return {"type": "auto"}
    if tool_choice == "none":
        return {"type": "none"}
    if tool_choice == "required" or tool_choice == "any":
        return {"type": "any"}
    # Treat anything else as a forced tool name.
    return {"type": "tool", "name": tool_choice}


def _anthropic_text_from_blocks(blocks: Sequence[Any]) -> str:
    """Concatenate ``text`` content blocks from an Anthropic response."""
    parts: list[str] = []
    for block in blocks:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_val = getattr(block, "text", None)
            if isinstance(text_val, str):
                parts.append(text_val)
    return "".join(parts)


def _anthropic_response_to_assistant_turn(resp: Any) -> AssistantTurn:
    """Decode an Anthropic ``Message`` response into :class:`AssistantTurn`."""
    blocks = getattr(resp, "content", None) or []
    text = _anthropic_text_from_blocks(blocks)
    decoded_calls: list[AssistantToolCall] = []
    for block in blocks:
        if getattr(block, "type", None) != "tool_use":
            continue
        call_id = getattr(block, "id", "")
        name = getattr(block, "name", "")
        raw_input = getattr(block, "input", None)
        if isinstance(raw_input, dict):
            arguments: dict[str, Any] = raw_input
        elif isinstance(raw_input, str):
            try:
                parsed = json.loads(raw_input)
            except (json.JSONDecodeError, TypeError):
                parsed = {}
            arguments = parsed if isinstance(parsed, dict) else {}
        else:
            arguments = {}
        decoded_calls.append(AssistantToolCall(id=call_id, name=name, arguments=arguments))
    return AssistantTurn(content=text, tool_calls=tuple(decoded_calls))
