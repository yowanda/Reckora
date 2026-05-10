"""Integration tests for the per-user settings endpoints.

These exercise the whole FastAPI stack (auth -> deps -> repository ->
encryption) so a regression anywhere along the chain surfaces here.
The ``authed_client`` fixture (see ``conftest.py``) registers user
``alice`` and primes a bearer token so each test starts authenticated.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_get_settings_defaults_to_no_keys(authed_client: TestClient) -> None:
    response = authed_client.get("/api/v1/users/me/settings")
    assert response.status_code == 200
    assert response.json() == {"has_agentrouter_key": False}


def test_put_then_get_marks_key_present(authed_client: TestClient) -> None:
    put = authed_client.put(
        "/api/v1/users/me/settings",
        json={"agentrouter_api_key": "sk-or-v1-secret"},
    )
    assert put.status_code == 200
    assert put.json() == {"has_agentrouter_key": True}

    get = authed_client.get("/api/v1/users/me/settings")
    assert get.status_code == 200
    assert get.json() == {"has_agentrouter_key": True}


def test_plaintext_key_never_echoed_in_responses(authed_client: TestClient) -> None:
    """Defence in depth: GET / PUT must not leak the plaintext key."""
    secret = "sk-or-v1-not-leaked-please"
    put = authed_client.put(
        "/api/v1/users/me/settings",
        json={"agentrouter_api_key": secret},
    )
    assert put.status_code == 200
    assert secret not in put.text

    get = authed_client.get("/api/v1/users/me/settings")
    assert secret not in get.text


def test_empty_string_clears_saved_key(authed_client: TestClient) -> None:
    authed_client.put(
        "/api/v1/users/me/settings",
        json={"agentrouter_api_key": "sk-or-v1-temp"},
    )
    cleared = authed_client.put(
        "/api/v1/users/me/settings",
        json={"agentrouter_api_key": ""},
    )
    assert cleared.status_code == 200
    assert cleared.json() == {"has_agentrouter_key": False}


def test_unauthenticated_get_returns_401(client: TestClient) -> None:
    response = client.get("/api/v1/users/me/settings")
    assert response.status_code == 401


def test_unauthenticated_put_returns_401(client: TestClient) -> None:
    response = client.put(
        "/api/v1/users/me/settings",
        json={"agentrouter_api_key": "x"},
    )
    assert response.status_code == 401


def test_extra_fields_rejected(authed_client: TestClient) -> None:
    """``extra='forbid'`` keeps the schema tight against typos."""
    response = authed_client.put(
        "/api/v1/users/me/settings",
        json={"agentrouter_api_key": "x", "unknown_field": "no"},
    )
    assert response.status_code == 422


def test_oversize_key_rejected(authed_client: TestClient) -> None:
    """Hard cap protects against accidental file uploads as keys."""
    response = authed_client.put(
        "/api/v1/users/me/settings",
        json={"agentrouter_api_key": "x" * 600},
    )
    assert response.status_code == 422
