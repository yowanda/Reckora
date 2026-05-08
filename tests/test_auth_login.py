"""Unit tests for ``reckora.auth.login.interactive_login``."""

from __future__ import annotations

import socket
import threading
import urllib.request
from collections.abc import Iterator

import httpx
import pytest

from reckora.auth.login import OAuthLoginError, interactive_login
from reckora.auth.oauth import TOKEN_URL


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def port() -> int:
    return _free_port()


def _drive_callback(port: int, *, code: str, state: str) -> threading.Thread:
    """Spin up a thread that waits briefly then hits the callback URL."""

    def _go() -> None:
        # Tiny sleep to give the server time to bind. ``serve_forever``
        # is racy with our hit-the-URL helper if the port has been
        # bound but the listen loop isn't yet running.
        import time

        time.sleep(0.1)
        url = f"http://127.0.0.1:{port}/auth/callback?code={code}&state={state}"
        try:
            urllib.request.urlopen(url, timeout=5).read()
        except Exception:
            return

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    return t


def _mock_token_transport(*, expected_code: str, expected_verifier: str) -> httpx.MockTransport:
    """An httpx ``MockTransport`` that mimics the OpenAI token endpoint."""

    def _handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == TOKEN_URL
        assert request.method == "POST"
        body = dict(httpx.QueryParams(request.read().decode()).multi_items())
        assert body["grant_type"] == "authorization_code"
        assert body["code"] == expected_code
        assert body["code_verifier"] == expected_verifier
        return httpx.Response(
            status_code=200,
            json={
                "access_token": "atk-from-mock",
                "refresh_token": "rtk-from-mock",
                "id_token": "idt-from-mock",
                "expires_in": 3600,
            },
        )

    return httpx.MockTransport(_handler)


def _http_client_factory(transport: httpx.MockTransport) -> Iterator[None]:
    """Convenience wrapper to build a ``httpx.AsyncClient`` factory."""
    return lambda: httpx.AsyncClient(transport=transport)  # type: ignore[return-value]


async def test_interactive_login_drives_full_flow(port: int) -> None:
    opened: list[str] = []

    def _opener(url: str) -> None:
        opened.append(url)
        # Simulate the user clicking through and the browser hitting
        # the callback. We drive this off the main event loop so the
        # ``server.wait`` call below blocks normally.
        _drive_callback(port, code="auth-code-1", state="state-fixed")

    transport = _mock_token_transport(
        expected_code="auth-code-1",
        expected_verifier="v" * 64,
    )

    creds = await interactive_login(
        open_browser=_opener,
        port=port,
        timeout=5.0,
        http_client_factory=lambda: httpx.AsyncClient(transport=transport),
        code_verifier="v" * 64,
        state="state-fixed",
    )

    # Browser opener was called exactly once with the authorize URL.
    assert len(opened) == 1
    assert "auth.openai.com/oauth/authorize" in opened[0]
    assert "state=state-fixed" in opened[0]

    assert creds.access_token == "atk-from-mock"
    assert creds.refresh_token == "rtk-from-mock"
    assert creds.id_token == "idt-from-mock"


async def test_interactive_login_fails_when_port_busy(port: int) -> None:
    """A second login attempt while the first is in flight must error."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port))
    sock.listen(1)
    try:
        with pytest.raises(OAuthLoginError, match="already in use"):
            await interactive_login(
                open_browser=lambda _url: None,
                port=port,
                timeout=1.0,
            )
    finally:
        sock.close()


async def test_interactive_login_surfaces_callback_error(port: int) -> None:
    def _opener(url: str) -> None:
        # Simulate the user hitting "Cancel" — provider redirects
        # back with ``error=access_denied``.
        def _go() -> None:
            import time

            time.sleep(0.1)
            # The server replies with HTTP 400 on the error path, and
            # ``urlopen`` raises ``HTTPError`` for 4xx by default — we
            # don't care about the response body here, only that the
            # request reached the server, so swallow any exception so
            # the test thread can exit cleanly without tripping
            # pytest's unraisable-exception watcher.
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/auth/callback?"
                    f"error=access_denied&error_description=user+denied",
                    timeout=5,
                ).read()
            except Exception:
                return

        threading.Thread(target=_go, daemon=True).start()

    with pytest.raises(OAuthLoginError, match="user denied"):
        await interactive_login(
            open_browser=_opener,
            port=port,
            timeout=5.0,
            code_verifier="v" * 64,
            state="state-fixed",
        )


async def test_interactive_login_surfaces_token_4xx(port: int) -> None:
    def _opener(url: str) -> None:
        _drive_callback(port, code="bad-code", state="state-fixed")

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=400,
            json={"error": "invalid_grant", "error_description": "code expired"},
        )

    transport = httpx.MockTransport(_handler)

    with pytest.raises(OAuthLoginError, match="token exchange failed"):
        await interactive_login(
            open_browser=_opener,
            port=port,
            timeout=5.0,
            http_client_factory=lambda: httpx.AsyncClient(transport=transport),
            code_verifier="v" * 64,
            state="state-fixed",
        )


async def test_interactive_login_times_out_if_browser_never_returns(port: int) -> None:
    with pytest.raises(OAuthLoginError, match="timeout"):
        await interactive_login(
            open_browser=lambda _url: None,
            port=port,
            timeout=0.2,
            code_verifier="v" * 64,
            state="state-fixed",
        )
