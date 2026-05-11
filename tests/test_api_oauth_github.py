"""GitHub OAuth social-login flow tests.

The flow lives under ``/api/v1/auth/oauth/github`` and we exercise it
end-to-end with ``pytest-httpx`` mocking GitHub's token and user
endpoints so the tests stay deterministic and fast.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from fastapi.testclient import TestClient
from pytest_httpx import HTTPXMock

from reckora.orchestrator import Orchestrator
from reckora_api.auth.oauth_github import issue_state_token
from reckora_api.config import APISettings
from reckora_api.main import create_app
from tests.conftest import _FakeCollector


@pytest.fixture
def oauth_settings(tmp_path: Path) -> APISettings:
    """API settings with GitHub OAuth configured."""
    return APISettings(
        jwt_secret=secrets.token_urlsafe(32),
        jwt_ttl_seconds=3600,
        db_path=str(tmp_path / "reckora.db"),
        cors_origins_raw="http://localhost:5173",
        docs_enabled=True,
        oauth_github_client_id="test-client-id",
        oauth_github_client_secret="test-client-secret",
        oauth_github_redirect_url="http://api.example.test/api/v1/auth/oauth/github/callback",
        frontend_url="http://app.example.test",
    )


@pytest.fixture
def oauth_client(oauth_settings: APISettings) -> Iterator[TestClient]:
    """TestClient with GitHub OAuth fully configured."""
    app = create_app(
        oauth_settings,
        orchestrator_factory=lambda: Orchestrator([_FakeCollector()]),
    )
    # ``follow_redirects=False`` keeps the 307s from being auto-followed
    # so we can assert on the ``Location`` header.
    with TestClient(app, follow_redirects=False) as c:
        yield c


def test_providers_advertises_github_when_configured(oauth_client: TestClient) -> None:
    response = oauth_client.get("/api/v1/auth/oauth/providers")
    assert response.status_code == 200
    assert response.json() == {"github": True}


def test_providers_advertises_no_github_when_unconfigured(client: TestClient) -> None:
    response = client.get("/api/v1/auth/oauth/providers")
    assert response.status_code == 200
    assert response.json() == {"github": False}


def test_login_returns_503_when_unconfigured(client: TestClient) -> None:
    response = client.get("/api/v1/auth/oauth/github/login")
    assert response.status_code == 503


def test_callback_returns_503_when_unconfigured(client: TestClient) -> None:
    response = client.get("/api/v1/auth/oauth/github/callback?code=x&state=y")
    assert response.status_code == 503


def test_login_redirects_to_github_authorize(oauth_client: TestClient) -> None:
    response = oauth_client.get("/api/v1/auth/oauth/github/login")
    assert response.status_code == 307
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.scheme == "https"
    assert parsed.netloc == "github.com"
    assert parsed.path == "/login/oauth/authorize"
    query = parse_qs(parsed.query)
    assert query["client_id"] == ["test-client-id"]
    assert query["redirect_uri"] == ["http://api.example.test/api/v1/auth/oauth/github/callback"]
    assert query["scope"] == ["read:user user:email"]
    assert "state" in query
    assert query["state"][0]


def test_login_round_trips_safe_next_in_state(
    oauth_client: TestClient,
    oauth_settings: APISettings,
) -> None:
    response = oauth_client.get("/api/v1/auth/oauth/github/login?next=/subjects/abc")
    state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
    payload = jwt.decode(state, oauth_settings.jwt_secret, algorithms=["HS256"])
    assert payload["next"] == "/subjects/abc"
    assert payload["type"] == "github_oauth_state"


def test_login_rejects_open_redirect_in_next(
    oauth_client: TestClient,
    oauth_settings: APISettings,
) -> None:
    response = oauth_client.get("/api/v1/auth/oauth/github/login?next=https://evil.example.com/")
    state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
    payload = jwt.decode(state, oauth_settings.jwt_secret, algorithms=["HS256"])
    # Open-redirect attempts should fall back to the default landing page.
    assert payload["next"] == "/subjects"


def test_callback_rejects_invalid_state(oauth_client: TestClient) -> None:
    response = oauth_client.get("/api/v1/auth/oauth/github/callback?code=ok&state=not-a-jwt")
    assert response.status_code == 400


def test_callback_rejects_state_signed_with_wrong_secret(
    oauth_client: TestClient,
) -> None:
    # State signed with an unrelated secret should be rejected even
    # though it is structurally a valid JWT.
    bad_state = issue_state_token(
        secret=secrets.token_urlsafe(32),
        next_path="/subjects",
    )
    response = oauth_client.get(f"/api/v1/auth/oauth/github/callback?code=ok&state={bad_state}")
    assert response.status_code == 400


def test_callback_rejects_missing_code(oauth_client: TestClient) -> None:
    response = oauth_client.get("/api/v1/auth/oauth/github/callback?state=anything")
    assert response.status_code == 400


def test_callback_surfaces_github_error(oauth_client: TestClient) -> None:
    response = oauth_client.get(
        "/api/v1/auth/oauth/github/callback?error=access_denied"
        "&error_description=user+denied+the+request"
    )
    assert response.status_code == 400
    assert "denied" in response.json()["detail"]


def _mock_github_happy_path(
    httpx_mock: HTTPXMock,
    *,
    user_id: int = 42,
    login: str = "alice",
    email: str | None = "alice@example.test",
) -> None:
    httpx_mock.add_response(
        url="https://github.com/login/oauth/access_token",
        method="POST",
        json={"access_token": "gh-access-token", "token_type": "bearer"},
    )
    httpx_mock.add_response(
        url="https://api.github.com/user",
        method="GET",
        json={"id": user_id, "login": login, "email": email},
    )
    if email is None:
        # The collector falls back to ``/user/emails`` when the profile
        # email is private. Stub a 403 to simulate the user denying the
        # ``user:email`` scope — the code path under test should still
        # succeed (with ``email=None``) instead of crashing.
        httpx_mock.add_response(
            url="https://api.github.com/user/emails",
            method="GET",
            status_code=403,
            json={"message": "scope user:email not granted"},
        )


def test_callback_creates_user_and_redirects_with_token(
    oauth_client: TestClient,
    oauth_settings: APISettings,
    httpx_mock: HTTPXMock,
) -> None:
    _mock_github_happy_path(httpx_mock)
    state = issue_state_token(
        secret=oauth_settings.jwt_secret,
        next_path="/subjects",
    )
    response = oauth_client.get(f"/api/v1/auth/oauth/github/callback?code=auth-code&state={state}")
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("http://app.example.test/auth/callback#")
    fragment = location.split("#", 1)[1]
    frag = parse_qs(fragment)
    assert "token" in frag
    assert frag["token"][0]
    assert frag["next"] == ["/subjects"]

    # The token must decode against the API's JWT secret and point at
    # the newly-created user.
    decoded = jwt.decode(
        frag["token"][0],
        oauth_settings.jwt_secret,
        algorithms=[oauth_settings.jwt_algorithm],
    )
    assert decoded["sub"].isdigit()

    # And that user must be visible to the rest of the API.
    me_resp = oauth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {frag['token'][0]}"},
    )
    assert me_resp.status_code == 200, me_resp.text
    body = me_resp.json()
    assert body["username"] == "alice"
    assert body["role"] == "viewer"


def test_callback_logs_existing_github_user_back_in(
    oauth_client: TestClient,
    oauth_settings: APISettings,
    httpx_mock: HTTPXMock,
) -> None:
    # First trip: create the user.
    _mock_github_happy_path(httpx_mock, user_id=1001, login="bob", email=None)
    state = issue_state_token(secret=oauth_settings.jwt_secret, next_path="/subjects")
    first = oauth_client.get(f"/api/v1/auth/oauth/github/callback?code=c1&state={state}")
    assert first.status_code == 307

    # Second trip with the same GitHub id should reuse the same Reckora
    # row even though the GitHub login changes (handle rename).
    _mock_github_happy_path(httpx_mock, user_id=1001, login="bob-renamed", email=None)
    state2 = issue_state_token(secret=oauth_settings.jwt_secret, next_path="/subjects")
    second = oauth_client.get(f"/api/v1/auth/oauth/github/callback?code=c2&state={state2}")
    assert second.status_code == 307

    # Both tokens must point at the same ``sub`` (Reckora user id).
    token1 = parse_qs(first.headers["location"].split("#", 1)[1])["token"][0]
    token2 = parse_qs(second.headers["location"].split("#", 1)[1])["token"][0]
    sub1 = jwt.decode(token1, oauth_settings.jwt_secret, algorithms=["HS256"])["sub"]
    sub2 = jwt.decode(token2, oauth_settings.jwt_secret, algorithms=["HS256"])["sub"]
    assert sub1 == sub2


def test_callback_derives_unique_username_on_login_collision(
    oauth_client: TestClient,
    oauth_settings: APISettings,
    httpx_mock: HTTPXMock,
) -> None:
    # Pre-create a password-only user with the same username GitHub
    # would otherwise pick. The OAuth callback must fall back to a
    # suffixed username instead of failing.
    oauth_client.post(
        "/api/v1/auth/register",
        json={"username": "carol", "password": "supersecret123"},
    )
    _mock_github_happy_path(httpx_mock, user_id=7777, login="carol")
    state = issue_state_token(secret=oauth_settings.jwt_secret, next_path="/subjects")
    response = oauth_client.get(f"/api/v1/auth/oauth/github/callback?code=c&state={state}")
    assert response.status_code == 307
    token = parse_qs(response.headers["location"].split("#", 1)[1])["token"][0]
    me_resp = oauth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["username"] == "carol-gh7777"


def test_oauth_user_cannot_log_in_with_password_grant(
    oauth_client: TestClient,
    oauth_settings: APISettings,
    httpx_mock: HTTPXMock,
) -> None:
    """OAuth-only accounts store an unusable hash → password grant fails."""
    _mock_github_happy_path(httpx_mock, user_id=2024, login="dave", email=None)
    state = issue_state_token(secret=oauth_settings.jwt_secret, next_path="/subjects")
    assert (
        oauth_client.get(f"/api/v1/auth/oauth/github/callback?code=c&state={state}").status_code
        == 307
    )
    # No password was ever set → /auth/token must refuse to issue a JWT.
    token_resp = oauth_client.post(
        "/api/v1/auth/token",
        data={"username": "dave", "password": "oauth"},
    )
    assert token_resp.status_code == 401
    token_resp = oauth_client.post(
        "/api/v1/auth/token",
        data={"username": "dave", "password": ""},
    )
    assert token_resp.status_code == 422


def test_callback_502s_on_github_token_failure(
    oauth_client: TestClient,
    oauth_settings: APISettings,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url="https://github.com/login/oauth/access_token",
        method="POST",
        status_code=400,
        json={"error": "bad_verification_code"},
    )
    state = issue_state_token(secret=oauth_settings.jwt_secret, next_path="/subjects")
    response = oauth_client.get(f"/api/v1/auth/oauth/github/callback?code=bad&state={state}")
    assert response.status_code == 502
