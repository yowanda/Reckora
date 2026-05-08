"""High-level orchestrator that runs the full ChatGPT OAuth flow.

The pieces that compose into ``interactive_login``:

1. Generate a PKCE verifier + challenge and a ``state`` nonce.
2. Stand up a one-shot HTTP server on ``127.0.0.1:1455`` (the
   callback URL whitelisted in OpenAI's app registration).
3. Open the user's browser at ``auth.openai.com/oauth/authorize``
   with our PKCE challenge + ``state`` riding along.
4. Wait (with a generous timeout) for the browser to come back with
   ``code``. The server validates ``state`` matches our nonce.
5. POST the ``code`` + verifier to ``auth.openai.com/oauth/token``
   and turn the response into :class:`OAuthCredentials`.

We deliberately keep this module thin so it can be reused by both
the CLI (``reckora auth login``) and any future programmatic entry
points (``reckora_api`` admin endpoints, integration tests against a
mock IdP) without dragging Typer / FastAPI imports along.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from typing import cast

import httpx

from .oauth import (
    CALLBACK_PORT,
    OAuthCredentials,
    build_authorize_url,
    exchange_code,
)
from .pkce import generate_code_challenge, generate_code_verifier
from .server import CallbackServer, is_port_free

# Default browser opener: the stdlib helper. Type cast because
# ``webbrowser.open`` returns ``bool`` and we don't actually care.
# Indirected so tests can inject a stub that records the URL instead
# of poking the real desktop browser.
_DEFAULT_TIMEOUT_SECONDS = 300.0


class OAuthLoginError(RuntimeError):
    """Raised when the OAuth flow fails for any reason.

    Distinct exception class so CLI / API callers can surface a
    user-friendly message and exit cleanly without catching every
    ``RuntimeError`` in the call stack.
    """


def _default_open_browser(url: str) -> None:
    # Local import so importing :mod:`reckora.auth.login` doesn't
    # initialise the (slow) stdlib ``webbrowser`` registry on plain
    # ``import reckora`` — a notable cost on macOS where it shells out
    # to ``defaults read``.
    import webbrowser

    webbrowser.open(url, new=1, autoraise=True)


async def interactive_login(
    *,
    open_browser: Callable[[str], None] | None = None,
    port: int = CALLBACK_PORT,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    code_verifier: str | None = None,
    state: str | None = None,
) -> OAuthCredentials:
    """Drive the full OAuth flow end-to-end and return credentials.

    Parameters
    ----------
    open_browser:
        Override how the authorize URL is opened. Tests pass a
        recorder; production uses the stdlib ``webbrowser.open``.
    port:
        Local callback port. ``CALLBACK_PORT`` (1455) is the only
        value OpenAI's app registration accepts; the parameter exists
        so tests can swap to a ephemeral port without touching prod
        constants.
    timeout:
        Seconds to wait for the user to complete the browser flow.
        Defaults to 5 min; long enough for 2FA / SSO redirect chains.
    http_client_factory:
        Override how the ``httpx.AsyncClient`` for the token
        exchange call is constructed. Tests pass a factory that
        injects a mock transport.
    code_verifier / state:
        Override generated PKCE / state values. Used only in tests
        for determinism; production code lets the helpers generate
        cryptographically random values.

    Raises
    ------
    OAuthLoginError
        If the port is already in use, the user closes the browser
        without completing, the authorize endpoint returns ``error``,
        or the token exchange call fails.
    """
    if not is_port_free(port):
        raise OAuthLoginError(
            f"local port {port} is already in use — "
            "another reckora auth login (or another tool using the "
            "Codex OAuth client) is already in flight. "
            "Close it before retrying."
        )

    verifier = code_verifier or generate_code_verifier()
    challenge = generate_code_challenge(verifier)
    nonce = state or secrets.token_urlsafe(24)
    authorize_url = build_authorize_url(code_challenge=challenge, state=nonce)
    opener = open_browser or _default_open_browser

    server = CallbackServer(port=port, expected_state=nonce)
    with server.start():
        opener(authorize_url)
        result = server.wait(timeout=timeout)

    if result.error or not result.code:
        raise OAuthLoginError(
            f"OAuth callback failed: {result.error or 'no authorization code received'}"
        )

    factory: Callable[[], httpx.AsyncClient] = http_client_factory or httpx.AsyncClient
    async with factory() as client:
        try:
            return await exchange_code(result.code, verifier, client=client)
        except httpx.HTTPStatusError as exc:
            # Surface the upstream body — auth.openai.com responds
            # with structured ``error`` / ``error_description`` JSON
            # that's much more useful than a bare 4xx.
            body = exc.response.text
            raise OAuthLoginError(
                f"token exchange failed ({exc.response.status_code}): {body}"
            ) from exc


# Re-export for callers that import from ``reckora.auth.login``.
__all__ = [
    "OAuthLoginError",
    "interactive_login",
]


# Static-typing nudge: declare the closure return type explicitly so
# mypy doesn't downgrade ``opener`` to ``Callable[..., Any]``.
_: Callable[[str], None] = _default_open_browser
__: Callable[[str], Awaitable[OAuthCredentials]] = cast(
    "Callable[[str], Awaitable[OAuthCredentials]]",
    interactive_login,
)
