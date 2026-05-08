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

import os
from pathlib import Path

import httpx
from openai import AsyncOpenAI

from ..auth.codex_client import complete_via_codex
from ..auth.oauth import OAuthCredentials, refresh_credentials
from ..auth.storage import load_credentials, save_credentials


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
        oauth_model: str = "gpt-5.1-codex-mini",
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
