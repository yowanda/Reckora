"""Auth-routing tests for ``reckora.reasoning.client.ReasoningClient``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.auth.oauth import CHATGPT_CODEX_BASE_URL, TOKEN_URL, OAuthCredentials
from reckora.auth.storage import save_credentials
from reckora.reasoning.client import ReasoningClient


def _sse(*events: str) -> bytes:
    return ("\n\n".join(f"data: {event}" for event in events) + "\n\n").encode("utf-8")


def _fresh_creds(
    *,
    access: str = "atk",
    refresh: str = "rtk",
    expires_in_seconds: int = 3600,
) -> OAuthCredentials:
    return OAuthCredentials(
        access_token=access,
        refresh_token=refresh,
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
        id_token="idt",
    )


async def test_raises_when_no_credentials_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = ReasoningClient(
        api_key=None,
        credentials_path=tmp_path / "missing.json",
    )
    with pytest.raises(RuntimeError, match="reckora auth login"):
        await client.complete("s", "u")


async def test_routes_to_oauth_when_only_credentials_present(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        content=_sse(
            '{"type":"response.output_text.delta","delta":"oauth-answer"}',
            '{"type":"response.completed"}',
        ),
        headers={"content-type": "text/event-stream"},
    )

    client = ReasoningClient(
        api_key=None,
        oauth_credentials=_fresh_creds(),
        oauth_model="gpt-5.1-codex-mini",
    )
    try:
        out = await client.complete("system-prompt", "user-prompt")
    finally:
        await client.aclose()

    assert out == "oauth-answer"

    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["Authorization"] == "Bearer atk"


async def test_api_key_wins_over_oauth_when_both_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a user has *both* ``OPENAI_API_KEY`` and a stored login,
    we keep the historical behaviour (API key wins)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")

    client = ReasoningClient(
        oauth_credentials=_fresh_creds(),
        oauth_model="gpt-5.1-codex-mini",
    )

    captured: dict[str, Any] = {}

    class _Choice:
        def __init__(self, text: str) -> None:
            self.message = type("M", (), {"content": text})

    class _Resp:
        def __init__(self, text: str) -> None:
            self.choices = [_Choice(text)]

    class _Completions:
        async def create(self, **kwargs: Any) -> _Resp:
            captured.update(kwargs)
            return _Resp("api-key-answer")

    class _Chat:
        completions = _Completions()

    class _OpenAIStub:
        chat = _Chat()

        async def close(self) -> None:
            return None

    # Inject directly so we don't actually call ``api.openai.com``.
    client._openai = _OpenAIStub()  # type: ignore[assignment]

    try:
        out = await client.complete("sys", "usr")
    finally:
        await client.aclose()

    assert out == "api-key-answer"
    assert captured["model"] == "gpt-4o-mini"
    assert captured["messages"][0]["content"] == "sys"
    assert captured["messages"][1]["content"] == "usr"


async def test_oauth_eagerly_refreshes_when_token_within_skew(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,
    tmp_path: Path,
) -> None:
    """Pre-emptive refresh when token is within the 2-min skew."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    creds_path = tmp_path / "auth.json"
    save_credentials(_fresh_creds(expires_in_seconds=60), path=creds_path)

    # Token endpoint mints a brand new pair.
    httpx_mock.add_response(
        method="POST",
        url=TOKEN_URL,
        json={
            "access_token": "atk-fresh",
            "refresh_token": "rtk-rotated",
            "expires_in": 3600,
        },
    )
    # Codex endpoint accepts the *fresh* token.
    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        content=_sse('{"type":"response.completed"}'),
        headers={"content-type": "text/event-stream"},
    )

    client = ReasoningClient(
        api_key=None,
        credentials_path=creds_path,
    )
    try:
        await client.complete("s", "u")
    finally:
        await client.aclose()

    # The Codex request must use the fresh token.
    requests = httpx_mock.get_requests()
    codex_requests = [r for r in requests if "/codex/responses" in str(r.url)]
    assert len(codex_requests) == 1
    assert codex_requests[0].headers["Authorization"] == "Bearer atk-fresh"

    # And the refreshed pair was written back to disk.
    persisted = creds_path.read_text()
    assert "atk-fresh" in persisted
    assert "rtk-rotated" in persisted


async def test_oauth_retries_once_on_401_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    creds_path = tmp_path / "auth.json"
    save_credentials(_fresh_creds(), path=creds_path)

    # First call → 401.
    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        status_code=401,
        json={"error": "invalid_token"},
    )
    # Refresh succeeds.
    httpx_mock.add_response(
        method="POST",
        url=TOKEN_URL,
        json={
            "access_token": "atk-after-401",
            "refresh_token": "rtk-after-401",
            "expires_in": 3600,
        },
    )
    # Retry succeeds.
    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        content=_sse(
            '{"type":"response.output_text.delta","delta":"recovered"}',
            '{"type":"response.completed"}',
        ),
        headers={"content-type": "text/event-stream"},
    )

    client = ReasoningClient(api_key=None, credentials_path=creds_path)
    try:
        out = await client.complete("s", "u")
    finally:
        await client.aclose()

    assert out == "recovered"

    codex_requests = [r for r in httpx_mock.get_requests() if "/codex/responses" in str(r.url)]
    assert len(codex_requests) == 2
    assert codex_requests[0].headers["Authorization"] == "Bearer atk"
    assert codex_requests[1].headers["Authorization"] == "Bearer atk-after-401"


async def test_oauth_second_401_is_fatal(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,
    tmp_path: Path,
) -> None:
    """If refresh fixes the token but the new one is *also* rejected,
    the second 401 must surface upstream."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    creds_path = tmp_path / "auth.json"
    save_credentials(_fresh_creds(), path=creds_path)

    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        status_code=401,
        json={"error": "invalid_token"},
    )
    httpx_mock.add_response(
        method="POST",
        url=TOKEN_URL,
        json={
            "access_token": "atk-after-401",
            "refresh_token": "rtk-rot",
            "expires_in": 3600,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        status_code=401,
        json={"error": "invalid_token"},
    )

    client = ReasoningClient(api_key=None, credentials_path=creds_path)
    try:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.complete("s", "u")
    finally:
        await client.aclose()
    assert exc_info.value.response.status_code == 401


async def test_oauth_403_does_not_trigger_refresh(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,
    tmp_path: Path,
) -> None:
    """Only 401 — the auth-layer error — triggers refresh. A 403 (e.g.
    the user's plan doesn't include Codex) must surface immediately."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    creds_path = tmp_path / "auth.json"
    save_credentials(_fresh_creds(), path=creds_path)

    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        status_code=403,
        json={"error": "plan_does_not_include_codex"},
    )

    client = ReasoningClient(api_key=None, credentials_path=creds_path)
    try:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.complete("s", "u")
    finally:
        await client.aclose()
    assert exc_info.value.response.status_code == 403


async def test_explicit_oauth_credentials_arent_persisted_on_refresh(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,
    tmp_path: Path,
) -> None:
    """When the embedder passes ``oauth_credentials=`` directly, refreshes
    must NOT write back to disk — the embedder owns persistence."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    creds_path = tmp_path / "auth.json"  # never created on disk

    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        status_code=401,
        json={"error": "invalid_token"},
    )
    httpx_mock.add_response(
        method="POST",
        url=TOKEN_URL,
        json={
            "access_token": "atk-fresh",
            "refresh_token": "rtk-rot",
            "expires_in": 3600,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        content=_sse('{"type":"response.completed"}'),
        headers={"content-type": "text/event-stream"},
    )

    client = ReasoningClient(
        api_key=None,
        oauth_credentials=_fresh_creds(),
        credentials_path=creds_path,
    )
    try:
        await client.complete("s", "u")
    finally:
        await client.aclose()

    assert not creds_path.exists()


def test_model_property_exposes_both_modes() -> None:
    client = ReasoningClient(
        api_key="ignored",
        model="gpt-4o-mini",
        oauth_model="gpt-5.1-codex-mini",
    )
    assert client.model == "gpt-4o-mini"
    assert client.oauth_model == "gpt-5.1-codex-mini"
