"""ChatGPT OAuth client constants and token endpoint helpers.

This module owns the static OpenAI Codex client metadata
(``CLIENT_ID``, redirect URI, scope, endpoints) plus the two
network calls that produce / refresh credentials. The flow is
authorization-code-with-PKCE — the public-client subset of OAuth 2.0
that doesn't require a server-side secret.

The ``CLIENT_ID``, port, and scope are *not* configurable. OpenAI's
app registration whitelists exactly one redirect URI
(``http://localhost:1455/auth/callback``), so any deviation 4xx-s at
the authorize step. The ChatGPT desktop app, OpenAI Codex CLI and a
handful of community wrappers (codex-auth, openai-oauth, ChatMock,
OpenClaw) all use the same constants; using anything else here means
running our own OAuth app, which would defeat the
"no-API-key-needed" point of this flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

# Public OpenAI Codex CLI client. Same value the Codex desktop app and
# the OpenAI ``@openai/codex`` CLI ship with — see e.g. the
# `codex_oauth` Rust crate (docs.rs) and the `openai-oauth` npm
# package. We have to use this exact id because it's what
# ``auth.openai.com`` recognises for the ChatGPT-subscription auth
# path; minting a separate app would route through the OpenAI
# Platform API-tier billing instead, which is the whole thing this
# flow exists to *avoid*.
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
SCOPE = "openid profile email offline_access"

# Hard-coded by OpenAI's app registration: only this redirect URI is
# whitelisted, so the local callback server *must* bind to this
# host:port pair. This is the same value the official Codex CLI uses.
CALLBACK_PORT = 1455
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/auth/callback"

# OpenAI-internal Codex Responses API. The OAuth ``access_token`` is
# accepted as a Bearer credential here (and *not* on
# ``api.openai.com``). All of codex-auth (PyPI), openai-oauth (npm),
# ChatMock and the OpenAI Codex CLI itself route through this base.
CHATGPT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


@dataclass(frozen=True)
class OAuthCredentials:
    """A successfully-minted set of ChatGPT OAuth tokens.

    All times are timezone-aware UTC. ``id_token`` is informational
    only — Reckora doesn't currently parse it for ``chatgpt_account_id``
    or plan tier, but we keep it on disk so future versions can
    surface "logged in as <email>" in ``reckora auth status`` without
    a re-login.
    """

    access_token: str
    refresh_token: str
    expires_at: datetime
    id_token: str | None = None

    def is_expired(self, *, skew: timedelta = timedelta(minutes=2)) -> bool:
        """``True`` iff ``access_token`` is within ``skew`` of expiry.

        We default to two minutes of skew so a request that races a
        token rollover still succeeds — the alternative is a 401
        on the first hop and a refresh-then-retry on the second,
        which is wasteful when we already have the fresh token in
        memory.
        """
        return datetime.now(UTC) + skew >= self.expires_at


def build_authorize_url(*, code_challenge: str, state: str) -> str:
    """Return the ``auth.openai.com/oauth/authorize`` URL to open in
    the user's browser.

    ``state`` is round-tripped back to the callback so the local
    server can reject responses that don't originate from the same
    flow it kicked off (CSRF-style protection). ``code_challenge`` is
    the S256-hashed PKCE challenge from
    :func:`reckora.auth.pkce.generate_code_challenge`.
    """
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(
    code: str,
    code_verifier: str,
    *,
    client: httpx.AsyncClient,
) -> OAuthCredentials:
    """Trade the ``code`` returned to the local callback for tokens.

    The token endpoint expects ``application/x-www-form-urlencoded``
    bodies (httpx's ``data=`` does this automatically). PKCE means we
    don't send a client secret — the ``code_verifier`` is the proof.
    """
    resp = await client.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        },
    )
    resp.raise_for_status()
    return _parse_token_response(resp.json())


async def refresh_credentials(
    refresh_token: str,
    *,
    client: httpx.AsyncClient,
) -> OAuthCredentials:
    """Mint a new pair of tokens from a long-lived ``refresh_token``.

    OpenAI rotates the refresh token on each refresh call, so the
    response carries a *new* refresh token — callers must persist
    the returned credentials in full or future refreshes will 4xx.
    """
    resp = await client.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh_token,
            "scope": SCOPE,
        },
    )
    resp.raise_for_status()
    return _parse_token_response(resp.json())


def _parse_token_response(payload: dict[str, Any]) -> OAuthCredentials:
    """Coerce a JSON token-endpoint payload into ``OAuthCredentials``.

    The ``expires_in`` field is seconds-from-now; we resolve it to an
    absolute ``expires_at`` here so callers don't have to track
    "when did I receive this" themselves. Missing ``access_token`` /
    ``refresh_token`` fields cause a ``KeyError`` rather than
    silently producing bogus credentials — both are required for
    Reckora to function.
    """
    expires_in = int(payload.get("expires_in", 0))
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    return OAuthCredentials(
        access_token=str(payload["access_token"]),
        refresh_token=str(payload["refresh_token"]),
        expires_at=expires_at,
        id_token=payload.get("id_token"),
    )
