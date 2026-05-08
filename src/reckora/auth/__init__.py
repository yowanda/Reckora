"""ChatGPT OAuth (PKCE) login for Reckora.

Reckora's reasoning layer accepts two auth modes:

1. ``OPENAI_API_KEY`` env var — billed against an OpenAI Platform
   account, talks to ``api.openai.com/v1/chat/completions``. This has
   always been Reckora's default.
2. **ChatGPT OAuth** — a PKCE flow against ``auth.openai.com`` whose
   ``access_token`` is used as a Bearer credential against
   ``chatgpt.com/backend-api/codex/responses`` (the same Codex
   Responses API the OpenAI Codex CLI uses). Lets a ChatGPT Plus / Pro
   subscriber drive Reckora's reasoning without having to provision a
   separate Platform billing account.

This package contains the building blocks: PKCE generation, the
authorize-URL builder, the token endpoint client, the local callback
HTTP server, on-disk credentials persistence and a high-level
``interactive_login`` orchestrator. The reasoning layer
(``reckora.reasoning.client.ReasoningClient``) consumes them through
the ``resolve_credentials`` helper and the ``CHATGPT_CODEX_BASE_URL``
constant.
"""

from __future__ import annotations

from .codex_client import CodexStreamError, complete_via_codex
from .login import OAuthLoginError, interactive_login
from .oauth import (
    AUTHORIZE_URL,
    CALLBACK_PORT,
    CHATGPT_CODEX_BASE_URL,
    CLIENT_ID,
    REDIRECT_URI,
    SCOPE,
    TOKEN_URL,
    OAuthCredentials,
    build_authorize_url,
    exchange_code,
    refresh_credentials,
)
from .pkce import generate_code_challenge, generate_code_verifier
from .server import CallbackResult, CallbackServer
from .storage import (
    DEFAULT_CREDENTIALS_PATH,
    delete_credentials,
    load_credentials,
    save_credentials,
)

__all__ = [
    "AUTHORIZE_URL",
    "CALLBACK_PORT",
    "CHATGPT_CODEX_BASE_URL",
    "CLIENT_ID",
    "DEFAULT_CREDENTIALS_PATH",
    "REDIRECT_URI",
    "SCOPE",
    "TOKEN_URL",
    "CallbackResult",
    "CallbackServer",
    "CodexStreamError",
    "OAuthCredentials",
    "OAuthLoginError",
    "build_authorize_url",
    "complete_via_codex",
    "delete_credentials",
    "exchange_code",
    "generate_code_challenge",
    "generate_code_verifier",
    "interactive_login",
    "load_credentials",
    "refresh_credentials",
    "save_credentials",
]
