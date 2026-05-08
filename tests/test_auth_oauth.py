"""Unit tests for ``reckora.auth.oauth`` — authorize URL + token endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.auth.oauth import (
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


def test_static_constants_match_codex_cli() -> None:
    """The constants must match the public OpenAI Codex CLI registration.

    Anything else and ``auth.openai.com`` will reject the authorize
    request as an unknown client / unwhitelisted redirect.
    """
    assert CLIENT_ID == "app_EMoamEEZ73f0CkXaXp7hrann"
    assert AUTHORIZE_URL == "https://auth.openai.com/oauth/authorize"
    assert TOKEN_URL == "https://auth.openai.com/oauth/token"
    assert SCOPE == "openid profile email offline_access"
    assert CALLBACK_PORT == 1455
    assert REDIRECT_URI == "http://localhost:1455/auth/callback"
    assert CHATGPT_CODEX_BASE_URL == "https://chatgpt.com/backend-api/codex"


def test_build_authorize_url_carries_required_params() -> None:
    url = build_authorize_url(code_challenge="abc123", state="state-xyz")
    parsed = urlparse(url)
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.openai.com"
    assert parsed.path == "/oauth/authorize"
    assert params["response_type"] == "code"
    assert params["client_id"] == CLIENT_ID
    assert params["redirect_uri"] == REDIRECT_URI
    assert params["scope"] == SCOPE
    assert params["code_challenge"] == "abc123"
    assert params["code_challenge_method"] == "S256"
    assert params["state"] == "state-xyz"


def test_build_authorize_url_url_encodes_state_with_special_chars() -> None:
    """A ``state`` value containing reserved chars must be percent-encoded."""
    url = build_authorize_url(code_challenge="x", state="a b/c?d=e")
    parsed = urlparse(url)
    state = parse_qs(parsed.query)["state"][0]
    # ``parse_qs`` decodes — so a successful round-trip proves we
    # escaped on the way in.
    assert state == "a b/c?d=e"


@pytest.mark.parametrize(
    ("expires_in", "expected_seconds"),
    [(3600, 3600), (60, 60), (28800, 28800)],
)
async def test_exchange_code_posts_correct_body_and_parses_response(
    httpx_mock: HTTPXMock,
    expires_in: int,
    expected_seconds: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=TOKEN_URL,
        json={
            "access_token": "atk-abc",
            "refresh_token": "rtk-abc",
            "id_token": "idt-abc",
            "expires_in": expires_in,
            "token_type": "Bearer",
        },
    )
    before = datetime.now(UTC)
    async with httpx.AsyncClient() as client:
        creds = await exchange_code(
            "auth-code-1",
            "verifier-1" * 5,
            client=client,
        )
    after = datetime.now(UTC)

    assert creds.access_token == "atk-abc"
    assert creds.refresh_token == "rtk-abc"
    assert creds.id_token == "idt-abc"
    # ``expires_at`` was resolved relative to "now"; allow a small
    # window for clock movement during the request.
    assert before + timedelta(seconds=expected_seconds - 1) <= creds.expires_at
    assert creds.expires_at <= after + timedelta(seconds=expected_seconds + 1)

    # Inspect the actual request body to confirm we send the spec-
    # mandated form fields.
    request = httpx_mock.get_request()
    assert request is not None
    body = dict(httpx.QueryParams(request.read().decode()).multi_items())
    assert body == {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": "auth-code-1",
        "redirect_uri": REDIRECT_URI,
        "code_verifier": "verifier-1" * 5,
    }


async def test_exchange_code_raises_on_4xx(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=TOKEN_URL,
        status_code=400,
        json={"error": "invalid_grant", "error_description": "code expired"},
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await exchange_code("bad", "verifier" * 6, client=client)


async def test_exchange_code_id_token_is_optional(httpx_mock: HTTPXMock) -> None:
    """Some token responses omit ``id_token`` — we must tolerate that."""
    httpx_mock.add_response(
        method="POST",
        url=TOKEN_URL,
        json={
            "access_token": "atk",
            "refresh_token": "rtk",
            "expires_in": 3600,
        },
    )
    async with httpx.AsyncClient() as client:
        creds = await exchange_code("c", "v" * 50, client=client)
    assert creds.id_token is None


async def test_refresh_credentials_posts_refresh_grant(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=TOKEN_URL,
        json={
            "access_token": "atk-new",
            "refresh_token": "rtk-rotated",
            "expires_in": 1800,
        },
    )
    async with httpx.AsyncClient() as client:
        creds = await refresh_credentials("rtk-old", client=client)

    assert creds.access_token == "atk-new"
    # OpenAI rotates refresh tokens — the new one *must* be persisted
    # by callers or the next refresh 4xxs.
    assert creds.refresh_token == "rtk-rotated"

    request = httpx_mock.get_request()
    assert request is not None
    body = dict(httpx.QueryParams(request.read().decode()).multi_items())
    assert body == {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": "rtk-old",
        "scope": SCOPE,
    }


async def test_refresh_credentials_raises_on_invalid_grant(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=TOKEN_URL,
        status_code=400,
        json={"error": "invalid_grant"},
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await refresh_credentials("rtk", client=client)


def test_oauth_credentials_is_expired_with_skew() -> None:
    """``is_expired`` returns True once we're inside the skew window."""
    now = datetime.now(UTC)
    fresh = OAuthCredentials(
        access_token="a",
        refresh_token="r",
        expires_at=now + timedelta(minutes=10),
    )
    stale = OAuthCredentials(
        access_token="a",
        refresh_token="r",
        expires_at=now + timedelta(seconds=30),
    )
    expired = OAuthCredentials(
        access_token="a",
        refresh_token="r",
        expires_at=now - timedelta(minutes=5),
    )
    assert not fresh.is_expired()
    # 30s remaining is *inside* the 2-min default skew → considered
    # expired so we proactively refresh.
    assert stale.is_expired()
    assert expired.is_expired()


def test_oauth_credentials_is_expired_respects_custom_skew() -> None:
    now = datetime.now(UTC)
    creds = OAuthCredentials(
        access_token="a",
        refresh_token="r",
        expires_at=now + timedelta(minutes=10),
    )
    # With a giant skew (1h), the 10-minute-out token should look
    # expired.
    assert creds.is_expired(skew=timedelta(hours=1))
