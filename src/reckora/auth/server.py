"""Local HTTP callback server that catches the OAuth redirect.

The OpenAI authorize endpoint redirects the user's browser to
``http://localhost:1455/auth/callback?code=...&state=...`` after
they approve. We need a one-shot HTTP server bound to that exact
host:port to receive the redirect and hand the ``code`` back to the
``interactive_login`` orchestrator.

Implementation notes:

* We use stdlib ``http.server`` in a daemon thread, *not* an asyncio
  HTTP server. The auth flow is rare and short-lived (seconds), so
  importing aiohttp / starlette just for this would be a net loss;
  starting a thread is cheap and the request shape is trivial enough
  to hand-parse.
* The server *only* accepts ``GET /auth/callback``; everything else
  404s. That keeps the surface tiny and makes hostile probing on the
  local port a no-op.
* We bind explicitly to ``127.0.0.1`` (not ``0.0.0.0``) so the
  callback isn't reachable from other machines on the LAN — only the
  local browser can hit it.
"""

from __future__ import annotations

import contextlib
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# Self-contained HTML so we don't depend on Jinja2 here. Kept short
# and styling-light because the only audience is the user staring at
# the page for ~1s before they alt-tab back to the terminal.
_SUCCESS_HTML = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<title>Reckora — login complete</title></head><body>"
    "<h1>You can close this tab.</h1>"
    "<p>Reckora has received your authorization code and is "
    "exchanging it for tokens in the terminal.</p>"
    "</body></html>"
)
_ERROR_HTML_TEMPLATE = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<title>Reckora — login error</title></head><body>"
    "<h1>Login failed.</h1><p>{message}</p>"
    "<p>Return to the terminal for details.</p></body></html>"
)


@dataclass(frozen=True)
class CallbackResult:
    """Outcome of waiting on the OAuth callback.

    Exactly one of ``code`` / ``error`` is set: a successful redirect
    populates ``code`` (and ``state`` for verification by the caller),
    a failure populates ``error`` (mirroring the OAuth ``error`` /
    ``error_description`` query params if the upstream redirected
    with them, otherwise a Reckora-side description of what went
    wrong locally).
    """

    code: str | None = None
    state: str | None = None
    error: str | None = None


class CallbackServer:
    """One-shot HTTP server that captures a single OAuth redirect.

    Use as::

        server = CallbackServer(port=CALLBACK_PORT, expected_state=state)
        with server.start():
            ... # open browser to authorize URL
            result = server.wait(timeout=300.0)
        # server is fully stopped here

    The ``expected_state`` argument lets the server reject any
    redirect whose ``state`` parameter doesn't match the value we
    sent on the authorize request — the OAuth-spec-mandated CSRF
    guard. Mismatched states are reported via ``CallbackResult.error``
    rather than silently succeeding.
    """

    def __init__(
        self,
        *,
        port: int,
        expected_state: str,
        path: str = "/auth/callback",
        host: str = "127.0.0.1",
    ) -> None:
        self._port = port
        self._expected_state = expected_state
        self._path = path
        self._host = host
        self._result: CallbackResult | None = None
        self._completed = threading.Event()
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @contextlib.contextmanager
    def start(self) -> Iterator[CallbackServer]:
        """Bind the listening socket and run the server in a daemon
        thread. Yields ``self`` so callers can use ``with`` to bound
        the server's lifetime to the login attempt."""
        if self._server is not None:
            raise RuntimeError("callback server already started")

        outer = self

        class _Handler(BaseHTTPRequestHandler):
            # Silence the noisy stderr access log; the user is reading
            # terminal output, not server logs.
            def log_message(self, fmt: str, *args: object) -> None:
                # ``BaseHTTPRequestHandler`` calls ``log_message`` for
                # every request; we discard the noise so the user
                # sees a clean terminal during the OAuth flow.
                del fmt, args
                return

            def do_GET(self) -> None:  # http.server contract uses CamelCase
                outer._handle_get(self)

        try:
            self._server = HTTPServer((self._host, self._port), _Handler)
        except OSError as exc:
            # The most common cause is "port already in use" — usually
            # because another login attempt is in flight in a second
            # terminal. Surface a clear error rather than a bare
            # ``OSError(98)``.
            raise OSError(
                f"could not bind {self._host}:{self._port}: {exc}. "
                "If another reckora auth login is in flight, finish or "
                "cancel it before starting a new one."
            ) from exc

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="reckora-oauth-callback",
            daemon=True,
        )
        self._thread.start()
        try:
            yield self
        finally:
            self._stop()

    def wait(self, *, timeout: float) -> CallbackResult:
        """Block up to ``timeout`` seconds for the callback to arrive.

        Returns the assembled :class:`CallbackResult`, or a result
        with ``error="timeout"`` if the user never completed the
        flow (e.g. they closed the tab).
        """
        if not self._completed.wait(timeout=timeout):
            return CallbackResult(error="timeout: no callback received")
        # ``_completed`` was set, so ``_result`` is non-None by
        # construction in ``_handle_get``.
        assert self._result is not None
        return self._result

    def _stop(self) -> None:
        """Tear down the listener and thread. Idempotent."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        """Per-request entrypoint shared between callback / 404 paths."""
        url = urlparse(handler.path)
        if url.path != self._path:
            handler.send_response(404)
            handler.send_header("Content-Type", "text/plain; charset=utf-8")
            handler.end_headers()
            handler.wfile.write(b"not found")
            return

        params = parse_qs(url.query)

        # ``parse_qs`` returns lists; the OAuth spec only ever uses
        # singular values for these query params so we take the first
        # element when present, otherwise ``None``.
        def _first(name: str) -> str | None:
            values = params.get(name)
            return values[0] if values else None

        code = _first("code")
        state = _first("state")
        error = _first("error")
        error_description = _first("error_description")

        if error:
            self._respond_error(handler, error_description or error)
            self._result = CallbackResult(error=error_description or error)
            self._completed.set()
            return

        if state != self._expected_state:
            msg = "state parameter mismatch (CSRF guard tripped)"
            self._respond_error(handler, msg)
            self._result = CallbackResult(error=msg)
            self._completed.set()
            return

        if not code:
            msg = "callback did not include an authorization code"
            self._respond_error(handler, msg)
            self._result = CallbackResult(error=msg)
            self._completed.set()
            return

        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.end_headers()
        handler.wfile.write(_SUCCESS_HTML.encode("utf-8"))
        self._result = CallbackResult(code=code, state=state)
        self._completed.set()

    @staticmethod
    def _respond_error(handler: BaseHTTPRequestHandler, message: str) -> None:
        handler.send_response(400)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.end_headers()
        handler.wfile.write(_ERROR_HTML_TEMPLATE.format(message=message).encode("utf-8"))


def is_port_free(port: int, *, host: str = "127.0.0.1") -> bool:
    """Probe whether ``host:port`` is bindable.

    Used as a pre-flight by ``interactive_login`` so we can fail fast
    with a clear "port 1455 is in use" message instead of a stack
    trace from inside the HTTP server thread.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
        return True
