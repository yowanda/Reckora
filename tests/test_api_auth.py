"""Auth surface: register / token / me, plus password and token edge cases."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from reckora_api.auth.passwords import hash_password, verify_password
from reckora_api.auth.tokens import create_token, decode_token
from reckora_api.config import APISettings


def test_healthz_does_not_require_auth(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_register_creates_user(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "supersecret123"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["username"] == "alice"
    assert "id" in body
    assert "password" not in body
    assert "password_hash" not in body


def test_register_rejects_duplicate_username(client: TestClient) -> None:
    payload = {"username": "alice", "password": "supersecret123"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    second = client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 409
    assert "already" in second.json()["detail"].lower()


def test_register_validates_username_format(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": "has spaces!", "password": "supersecret123"},
    )
    assert response.status_code == 422


def test_register_validates_password_length(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "short"},
    )
    assert response.status_code == 422


def test_login_returns_bearer_token(client: TestClient) -> None:
    client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "supersecret123"},
    )
    response = client.post(
        "/api/v1/auth/token",
        data={"username": "alice", "password": "supersecret123"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    assert body["access_token"]


def test_login_with_wrong_password_returns_401(client: TestClient) -> None:
    client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "supersecret123"},
    )
    response = client.post(
        "/api/v1/auth/token",
        data={"username": "alice", "password": "wrongpassword"},
    )
    assert response.status_code == 401


def test_login_with_unknown_user_returns_401(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/token",
        data={"username": "ghost", "password": "supersecret123"},
    )
    assert response.status_code == 401


def test_me_requires_token(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_returns_authenticated_user(authed_client: TestClient) -> None:
    response = authed_client.get("/api/v1/auth/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["username"] == "alice"


def test_me_with_invalid_token_returns_401(client: TestClient) -> None:
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not.a.real.jwt"},
    )
    assert response.status_code == 401


def test_me_with_expired_token_returns_401(client: TestClient, api_settings: APISettings) -> None:
    client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "supersecret123"},
    )
    expired_token = create_token(
        subject="1",
        secret=api_settings.jwt_secret,
        ttl_seconds=-10,
        algorithm=api_settings.jwt_algorithm,
    )
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401


def test_me_with_token_for_unknown_user_returns_401(
    client: TestClient,
    api_settings: APISettings,
) -> None:
    """Token signature is valid but `sub` points at a row that doesn't exist."""
    token = create_token(
        subject="999999",
        secret=api_settings.jwt_secret,
        algorithm=api_settings.jwt_algorithm,
    )
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_me_with_non_numeric_sub_returns_401(
    client: TestClient,
    api_settings: APISettings,
) -> None:
    """A malformed token (`sub` not a digit) should not crash the server."""
    token = create_token(
        subject="not-a-number",
        secret=api_settings.jwt_secret,
        algorithm=api_settings.jwt_algorithm,
    )
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_password_hash_round_trip() -> None:
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)
    assert not verify_password("correct horse battery staple", "$$$ not a real hash $$$")


def test_token_round_trip_carries_subject() -> None:
    secret = "x" * 32  # 32 bytes — RFC 7518 Section 3.2 minimum for HS256.
    token = create_token(subject="42", secret=secret)
    decoded = decode_token(token, secret=secret)
    assert decoded["sub"] == "42"
    assert decoded["exp"] > int(time.time())
