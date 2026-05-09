"""Phase 5 step 7 — per-dossier status (open / on_hold / closed).

Covers:

- Default state: a brand-new dossier reports ``open`` with no audit
  metadata (the implicit-default path of ``AccessRepository.get_status``).
- State transitions: open → on_hold → closed → open, including the
  ping-pong audit trail (timestamps move; ``updated_by`` follows
  the actor).
- Allow-list: any status outside ``{open, on_hold, closed}`` is 422.
- Authorisation:
    * Read: any reader (owner / sharer / assignee / admin).
    * Write: owner / admin only — sharers and assignees get 403.
- ``GET /api/v1/status`` aggregates visible dossiers into per-bucket
  counts; admins see global counts.
- Cascade: deleting a subject removes its status row.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from reckora_api.auth.models import Role
from reckora_api.auth.passwords import hash_password
from reckora_api.auth.repository import UserRepository
from reckora_api.config import APISettings

# --- helpers ---------------------------------------------------------------


def _login(client: TestClient, *, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/token",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


def _register(client: TestClient, *, username: str, password: str) -> int:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert response.status_code == 201, response.text
    user_id: int = response.json()["id"]
    return user_id


def _create_subject(client: TestClient, *, value: str = "alice") -> str:
    response = client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": value}},
    )
    assert response.status_code in (200, 201), response.text
    sid: str = response.json()["id"]
    return sid


def _put_status(client: TestClient, sid: str, value: str) -> dict[str, object]:
    response = client.put(
        f"/api/v1/subjects/{sid}/status",
        json={"status": value},
    )
    return {"status": response.status_code, "json": response.json()}


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def admin_token(client: TestClient, api_settings: APISettings) -> str:
    with UserRepository(api_settings.db_path) as repo:
        repo.create_user(
            username="root",
            password_hash=hash_password("rootsecret123"),
            role=Role.ADMIN,
        )
    return _login(client, username="root", password="rootsecret123")


@pytest.fixture
def trio_clients(
    client: TestClient,
) -> Iterator[tuple[TestClient, TestClient, TestClient]]:
    """Three viewer clients (alice, bob, carol) sharing one app."""
    _register(client, username="alice", password="alicepassword1")
    _register(client, username="bob", password="bobpassword12")
    _register(client, username="carol", password="carolpassword1")

    alice_token = _login(client, username="alice", password="alicepassword1")
    bob_token = _login(client, username="bob", password="bobpassword12")
    carol_token = _login(client, username="carol", password="carolpassword1")

    alice = TestClient(client.app)
    alice.headers["Authorization"] = f"Bearer {alice_token}"
    bob = TestClient(client.app)
    bob.headers["Authorization"] = f"Bearer {bob_token}"
    carol = TestClient(client.app)
    carol.headers["Authorization"] = f"Bearer {carol_token}"

    try:
        yield alice, bob, carol
    finally:
        alice.close()
        bob.close()
        carol.close()


# --- defaults --------------------------------------------------------------


def test_brand_new_dossier_is_implicitly_open(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    response = alice.get(f"/api/v1/subjects/{sid}/status")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "status": "open",
        "updated_by": None,
        "updated_at": None,
    }


# --- transitions -----------------------------------------------------------


def test_owner_can_transition_through_state_machine(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    on_hold = alice.put(
        f"/api/v1/subjects/{sid}/status",
        json={"status": "on_hold"},
    )
    assert on_hold.status_code == 200, on_hold.text
    on_hold_body = on_hold.json()
    assert on_hold_body["status"] == "on_hold"
    assert on_hold_body["updated_by"] == "alice"
    on_hold_at = on_hold_body["updated_at"]
    assert on_hold_at is not None

    closed = alice.put(
        f"/api/v1/subjects/{sid}/status",
        json={"status": "closed"},
    )
    assert closed.status_code == 200
    closed_body = closed.json()
    assert closed_body["status"] == "closed"
    assert closed_body["updated_at"] >= on_hold_at  # monotonic ISO

    # Ping-pong back to open materialises a row (we keep audit
    # metadata even on return-to-default).
    reopen = alice.put(
        f"/api/v1/subjects/{sid}/status",
        json={"status": "open"},
    )
    assert reopen.status_code == 200
    reopen_body = reopen.json()
    assert reopen_body["status"] == "open"
    assert reopen_body["updated_by"] == "alice"
    assert reopen_body["updated_at"] is not None


def test_status_transition_is_idempotent(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Re-PUTting the same status updates ``updated_at`` but
    otherwise leaves things alone."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    first = alice.put(
        f"/api/v1/subjects/{sid}/status",
        json={"status": "closed"},
    )
    second = alice.put(
        f"/api/v1/subjects/{sid}/status",
        json={"status": "closed"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "closed"
    assert second.json()["updated_at"] >= first.json()["updated_at"]


def test_get_returns_latest_audit_after_write(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    alice.put(f"/api/v1/subjects/{sid}/status", json={"status": "on_hold"})
    response = alice.get(f"/api/v1/subjects/{sid}/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "on_hold"
    assert body["updated_by"] == "alice"
    assert body["updated_at"] is not None


# --- allow-list ------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value", ["", "OPEN", "Open", "in_progress", "wontfix", "closed ", "open\n"]
)
def test_unknown_status_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
    bad_value: str,
) -> None:
    """Anything outside the allow-list (case-sensitive, no whitespace) is 422."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    response = alice.put(
        f"/api/v1/subjects/{sid}/status",
        json={"status": bad_value},
    )
    assert response.status_code == 422, response.text


def test_extra_fields_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    response = alice.put(
        f"/api/v1/subjects/{sid}/status",
        json={"status": "closed", "reason": "case solved"},
    )
    assert response.status_code == 422


# --- authorisation ---------------------------------------------------------


def test_sharer_can_read_but_not_write(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)

    share = alice.post(
        f"/api/v1/subjects/{sid}/share",
        json={"username": "bob"},
    )
    assert share.status_code in (200, 201), share.text

    listing = bob.get(f"/api/v1/subjects/{sid}/status")
    assert listing.status_code == 200
    assert listing.json()["status"] == "open"

    write = bob.put(
        f"/api/v1/subjects/{sid}/status",
        json={"status": "closed"},
    )
    assert write.status_code == 403


def test_assignee_can_read_but_not_write(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)

    assigned = alice.post(
        f"/api/v1/subjects/{sid}/assignees",
        json={"username": "bob"},
    )
    assert assigned.status_code in (200, 201), assigned.text

    listing = bob.get(f"/api/v1/subjects/{sid}/status")
    assert listing.status_code == 200

    write = bob.put(
        f"/api/v1/subjects/{sid}/status",
        json={"status": "on_hold"},
    )
    assert write.status_code == 403


def test_outsider_gets_404_not_403(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, carol = trio_clients
    sid = _create_subject(alice)

    listing = carol.get(f"/api/v1/subjects/{sid}/status")
    assert listing.status_code == 404

    write = carol.put(
        f"/api/v1/subjects/{sid}/status",
        json={"status": "closed"},
    )
    assert write.status_code == 404


def test_admin_can_change_status_on_any_subject(
    client: TestClient,
    trio_clients: tuple[TestClient, TestClient, TestClient],
    admin_token: str,
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    admin = TestClient(client.app)
    admin.headers["Authorization"] = f"Bearer {admin_token}"
    response = admin.put(
        f"/api/v1/subjects/{sid}/status",
        json={"status": "closed"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed"
    assert response.json()["updated_by"] == "root"
    admin.close()


def test_unknown_subject_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    response = alice.get("/api/v1/subjects/subj-does-not-exist/status")
    assert response.status_code == 404


def test_unauthenticated_request_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/subjects/subj-anything/status")
    assert response.status_code == 401


# --- catalog ---------------------------------------------------------------


def test_status_catalog_aggregates_visible_dossiers(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    s_open = _create_subject(alice, value="alice")
    s_hold = _create_subject(alice, value="bob")
    s_closed = _create_subject(alice, value="carol")

    alice.put(f"/api/v1/subjects/{s_hold}/status", json={"status": "on_hold"})
    alice.put(f"/api/v1/subjects/{s_closed}/status", json={"status": "closed"})

    catalog = alice.get("/api/v1/status")
    assert catalog.status_code == 200, catalog.text
    body = catalog.json()
    # Three dossiers, three buckets (one each).
    assert body == {"open": 1, "on_hold": 1, "closed": 1}
    # Sanity: the never-touched dossier is still in the open bucket.
    _ = s_open


def test_status_catalog_scoped_to_actor_visibility(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, carol = trio_clients
    sid = _create_subject(alice)
    alice.put(f"/api/v1/subjects/{sid}/status", json={"status": "closed"})

    initial = carol.get("/api/v1/status")
    assert initial.status_code == 200
    assert initial.json() == {}

    alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "carol"})
    after = carol.get("/api/v1/status")
    assert after.status_code == 200
    assert after.json() == {"closed": 1}


def test_admin_status_catalog_is_global(
    client: TestClient,
    trio_clients: tuple[TestClient, TestClient, TestClient],
    admin_token: str,
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    alice.put(f"/api/v1/subjects/{sid}/status", json={"status": "on_hold"})

    admin = TestClient(client.app)
    admin.headers["Authorization"] = f"Bearer {admin_token}"
    response = admin.get("/api/v1/status")
    assert response.status_code == 200
    assert response.json() == {"on_hold": 1}
    admin.close()


# --- cascade ---------------------------------------------------------------


def test_deleting_subject_removes_status_row(
    client: TestClient,
    trio_clients: tuple[TestClient, TestClient, TestClient],
    admin_token: str,
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    alice.put(f"/api/v1/subjects/{sid}/status", json={"status": "closed"})

    admin = TestClient(client.app)
    admin.headers["Authorization"] = f"Bearer {admin_token}"
    deleted = admin.delete(f"/api/v1/subjects/{sid}")
    assert deleted.status_code in (200, 204), deleted.text

    catalog = admin.get("/api/v1/status")
    assert catalog.status_code == 200
    assert catalog.json() == {}
    admin.close()
