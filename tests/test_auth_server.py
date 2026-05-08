"""Unit tests for the local OAuth callback server."""

from __future__ import annotations

import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from reckora.auth.server import CallbackResult, CallbackServer, is_port_free


def _free_port() -> int:
    """Return an OS-assigned ephemeral port that's free *right now*.

    There's a tiny race between picking the port and the server
    binding to it, but in practice the test process is the only one
    racing so this is fine for unit tests.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def port() -> int:
    return _free_port()


@pytest.fixture
def server_with_state(port: int) -> Iterator[tuple[CallbackServer, int]]:
    """Yield a started callback server and its port."""
    server = CallbackServer(port=port, expected_state="state-xyz")
    with server.start():
        yield server, port


def _hit(url: str) -> tuple[int, str]:
    """Open ``url`` and return ``(status, body)``.

    Uses ``urllib`` rather than httpx so we don't drag the async
    machinery into a sync server test.
    """
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return int(resp.status), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")


def test_callback_server_captures_code_on_happy_path(
    server_with_state: tuple[CallbackServer, int],
) -> None:
    server, port = server_with_state

    # Drive the redirect on a background thread so we can wait()
    # synchronously on the main thread.
    def _drive() -> None:
        _hit(f"http://127.0.0.1:{port}/auth/callback?code=abc123&state=state-xyz")

    threading.Thread(target=_drive, daemon=True).start()
    result = server.wait(timeout=5.0)

    assert result == CallbackResult(code="abc123", state="state-xyz")


def test_callback_server_rejects_state_mismatch_csrf(
    server_with_state: tuple[CallbackServer, int],
) -> None:
    server, port = server_with_state

    def _drive() -> None:
        _hit(f"http://127.0.0.1:{port}/auth/callback?code=abc&state=tampered")

    threading.Thread(target=_drive, daemon=True).start()
    result = server.wait(timeout=5.0)

    assert result.code is None
    assert result.error is not None
    assert "state" in result.error.lower()


def test_callback_server_propagates_oauth_error(
    server_with_state: tuple[CallbackServer, int],
) -> None:
    """An ``error=`` query param on the redirect must surface verbatim."""
    server, port = server_with_state

    def _drive() -> None:
        _hit(
            f"http://127.0.0.1:{port}/auth/callback?"
            f"error=access_denied&error_description=user+cancelled"
        )

    threading.Thread(target=_drive, daemon=True).start()
    result = server.wait(timeout=5.0)

    assert result.code is None
    # We prefer ``error_description`` for a more useful message.
    assert result.error == "user cancelled"


def test_callback_server_handles_missing_code(
    server_with_state: tuple[CallbackServer, int],
) -> None:
    """Redirect arrived but ``code`` was missing — surface a clear error."""
    server, port = server_with_state

    def _drive() -> None:
        _hit(f"http://127.0.0.1:{port}/auth/callback?state=state-xyz")

    threading.Thread(target=_drive, daemon=True).start()
    result = server.wait(timeout=5.0)

    assert result.code is None
    assert result.error is not None
    assert "authorization code" in result.error.lower()


def test_callback_server_404s_other_paths(port: int) -> None:
    """Anything other than ``GET /auth/callback`` must 404 cleanly."""
    server = CallbackServer(port=port, expected_state="state-xyz")
    with server.start():
        status, body = _hit(f"http://127.0.0.1:{port}/random-path")
    assert status == 404
    assert "not found" in body


def test_callback_server_times_out_when_no_redirect(port: int) -> None:
    server = CallbackServer(port=port, expected_state="state-xyz")
    with server.start():
        result = server.wait(timeout=0.2)
    assert result.code is None
    assert result.error is not None
    assert "timeout" in result.error.lower()


def test_cannot_double_start(port: int) -> None:
    server = CallbackServer(port=port, expected_state="x")
    with (
        server.start(),
        pytest.raises(RuntimeError, match="already started"),
        server.start(),
    ):
        pass  # pragma: no cover — should not be reached


def test_start_raises_clear_error_on_port_in_use(port: int) -> None:
    """Two ``CallbackServer``s on the same port should fail loudly."""
    occupier = CallbackServer(port=port, expected_state="x")
    second = CallbackServer(port=port, expected_state="y")
    with (
        occupier.start(),
        pytest.raises(OSError, match="reckora auth login"),
        second.start(),
    ):
        pass  # pragma: no cover — bind should fail before yielding


def test_is_port_free_distinguishes_bound_vs_free(port: int) -> None:
    assert is_port_free(port) is True
    server = CallbackServer(port=port, expected_state="x")
    with server.start():
        assert is_port_free(port) is False
    # Port should be reusable once the server stops.
    assert is_port_free(port) is True
