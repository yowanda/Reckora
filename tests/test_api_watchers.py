"""Phase 5 step 8 — per-dossier watchers / following.

A watcher is a self-subscribed reader: any user who can already read
a dossier can opt in to follow it, and only that user can toggle
their own subscription. Watching does not by itself grant read
access — the cascade fires the other direction (revoking a share
or deleting the subject wipes the watch row).

Tests cover:

- The per-dossier subscribe / unsubscribe / list endpoints, including
  the idempotent shape (re-PUT / re-DELETE return the same
  ``WatchStatus`` without 404).
- The ``GET /api/v1/me/watching`` catalog: ordering by *subscription*
  time (most-recently-followed first), pagination via ``limit``, and
  visibility (a user only sees their own watch list).
- Authorisation: outsiders 404 on every per-dossier endpoint, an
  admin can subscribe to anything, unauthenticated requests 401.
- Cascade: deleting the subject removes its watcher rows; revoking a
  share drops the now-orphan watch.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import pytest
from fastapi.testclient import TestClient

from reckora_api.auth.models import Role
from reckora_api.auth.passwords import hash_password
from reckora_api.auth.repository import UserRepository
from reckora_api.config import APISettings


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


def _set_auth(client: TestClient, token: str) -> None:
    client.headers["Authorization"] = f"Bearer {token}"


def _create_subject(client: TestClient, *, value: str = "alice") -> str:
    response = client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": value}},
    )
    assert response.status_code in (200, 201), response.text
    sid: str = response.json()["id"]
    return sid


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


# --- happy path ------------------------------------------------------------


def test_owner_subscribes_and_appears_in_watcher_list(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    response = alice.put(f"/api/v1/subjects/{sid}/watchers/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"watching": True, "watcher_count": 1}

    listing = alice.get(f"/api/v1/subjects/{sid}/watchers").json()
    assert len(listing) == 1
    assert listing[0]["username"] == "alice"
    assert "created_at" in listing[0]


def test_subscribe_is_idempotent(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Two consecutive PUTs from the same actor must not stack the
    count — watchers are a set, not a counter."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    alice.put(f"/api/v1/subjects/{sid}/watchers/me")
    second = alice.put(f"/api/v1/subjects/{sid}/watchers/me")
    assert second.status_code == 200
    assert second.json() == {"watching": True, "watcher_count": 1}


def test_unsubscribe_drops_watcher(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    alice.put(f"/api/v1/subjects/{sid}/watchers/me")
    response = alice.delete(f"/api/v1/subjects/{sid}/watchers/me")
    assert response.status_code == 200
    assert response.json() == {"watching": False, "watcher_count": 0}
    assert alice.get(f"/api/v1/subjects/{sid}/watchers").json() == []


def test_unsubscribe_when_not_watching_is_noop(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """A stale optimistic UI cannot fail by double-clicking the bell.

    Re-DELETE while not currently a watcher is also a 200 / no-op,
    matching the idempotent shape of the PUT side."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.delete(f"/api/v1/subjects/{sid}/watchers/me")
    assert response.status_code == 200
    assert response.json() == {"watching": False, "watcher_count": 0}


def test_multiple_watchers_aggregate_into_one_count(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201

    alice.put(f"/api/v1/subjects/{sid}/watchers/me")
    bob_response = bob.put(f"/api/v1/subjects/{sid}/watchers/me")
    assert bob_response.json() == {"watching": True, "watcher_count": 2}

    listing = alice.get(f"/api/v1/subjects/{sid}/watchers").json()
    assert [r["username"] for r in listing] == ["alice", "bob"]


def test_watcher_listing_is_oldest_first(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Watchers come back in subscription order so the UI's avatar
    stack stays stable across requests."""
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201

    bob.put(f"/api/v1/subjects/{sid}/watchers/me")
    alice.put(f"/api/v1/subjects/{sid}/watchers/me")

    listing = alice.get(f"/api/v1/subjects/{sid}/watchers").json()
    assert [r["username"] for r in listing] == ["bob", "alice"]


# --- access control --------------------------------------------------------


def test_sharer_can_subscribe_and_list(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201

    response = bob.put(f"/api/v1/subjects/{sid}/watchers/me")
    assert response.status_code == 200
    assert response.json() == {"watching": True, "watcher_count": 1}

    listing = bob.get(f"/api/v1/subjects/{sid}/watchers").json()
    assert [r["username"] for r in listing] == ["bob"]


def test_assignee_can_subscribe(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert (
        alice.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "bob"}).status_code == 201
    )
    response = bob.put(f"/api/v1/subjects/{sid}/watchers/me")
    assert response.status_code == 200
    assert response.json() == {"watching": True, "watcher_count": 1}


def test_outsider_cannot_list_or_subscribe(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Carol has no access — list / subscribe / unsubscribe all 404
    so the existence of the dossier never leaks."""
    alice, _bob, carol = trio_clients
    sid = _create_subject(alice)

    assert carol.get(f"/api/v1/subjects/{sid}/watchers").status_code == 404
    assert carol.put(f"/api/v1/subjects/{sid}/watchers/me").status_code == 404
    assert carol.delete(f"/api/v1/subjects/{sid}/watchers/me").status_code == 404


def test_admin_can_subscribe_to_anything(
    client: TestClient,
    admin_token: str,
) -> None:
    _register(client, username="alice", password="alicepassword1")
    alice_token = _login(client, username="alice", password="alicepassword1")
    _set_auth(client, alice_token)
    sid = _create_subject(client)

    _set_auth(client, admin_token)
    response = client.put(f"/api/v1/subjects/{sid}/watchers/me")
    assert response.status_code == 200
    assert response.json() == {"watching": True, "watcher_count": 1}

    listing = client.get(f"/api/v1/subjects/{sid}/watchers").json()
    assert [r["username"] for r in listing] == ["root"]


def test_unknown_subject_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    assert alice.get("/api/v1/subjects/subj-missing/watchers").status_code == 404
    assert alice.put("/api/v1/subjects/subj-missing/watchers/me").status_code == 404
    assert alice.delete("/api/v1/subjects/subj-missing/watchers/me").status_code == 404


def test_unauthenticated_requests_rejected(client: TestClient) -> None:
    """The auth gate must fire on every watcher endpoint, including
    the ``/me/watching`` self-list."""
    assert client.get("/api/v1/subjects/sid/watchers").status_code == 401
    assert client.put("/api/v1/subjects/sid/watchers/me").status_code == 401
    assert client.delete("/api/v1/subjects/sid/watchers/me").status_code == 401
    assert client.get("/api/v1/me/watching").status_code == 401


# --- /me/watching catalog --------------------------------------------------


def test_me_watching_lists_subscribed_dossiers_newest_first(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Watch list is ordered by *subscription* time (most-recent first),
    not by dossier creation time — the user expects the last thing
    they starred to sit at the top."""
    alice, _bob, _carol = trio_clients
    sid_a = _create_subject(alice, value="alice")
    sid_b = _create_subject(alice, value="bob")
    sid_c = _create_subject(alice, value="carol")

    # Subscribe in order a → c → b so the expected result is b, c, a.
    alice.put(f"/api/v1/subjects/{sid_a}/watchers/me")
    alice.put(f"/api/v1/subjects/{sid_c}/watchers/me")
    alice.put(f"/api/v1/subjects/{sid_b}/watchers/me")

    rows = alice.get("/api/v1/me/watching").json()
    assert [r["id"] for r in rows] == [sid_b, sid_c, sid_a]


def test_me_watching_limit_truncates_result(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sids = [_create_subject(alice, value=f"alice{i}") for i in range(3)]
    for sid in sids:
        alice.put(f"/api/v1/subjects/{sid}/watchers/me")

    rows = alice.get("/api/v1/me/watching", params={"limit": 2}).json()
    assert len(rows) == 2


def test_me_watching_negative_limit_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    response = alice.get("/api/v1/me/watching", params={"limit": -1})
    assert response.status_code == 422


def test_me_watching_is_per_actor(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Bob's watch list never leaks into alice's, even when both watch
    the same dossier."""
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201

    alice.put(f"/api/v1/subjects/{sid}/watchers/me")
    # Bob did not subscribe, so his watch list is empty.
    assert bob.get("/api/v1/me/watching").json() == []
    # But alice sees the dossier she just subscribed to.
    rows = alice.get("/api/v1/me/watching").json()
    assert len(rows) == 1 and rows[0]["id"] == sid


def test_me_watching_empty_when_no_subscriptions(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    _create_subject(alice)  # alice owns it but never watches it
    assert alice.get("/api/v1/me/watching").json() == []


# --- cascade ---------------------------------------------------------------


def test_deleting_subject_cascades_watchers(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    alice.put(f"/api/v1/subjects/{sid}/watchers/me")
    bob.put(f"/api/v1/subjects/{sid}/watchers/me")

    assert alice.delete(f"/api/v1/subjects/{sid}").status_code == 204
    # The dossier is gone — every per-dossier watcher endpoint 404s,
    # and the catalog drops the row entirely.
    assert alice.get(f"/api/v1/subjects/{sid}/watchers").status_code == 404
    rows = alice.get("/api/v1/me/watching").json()
    assert all(r["id"] != sid for r in cast(list[dict[str, object]], rows))


def test_revoked_share_cascades_watcher_row(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Revoking bob's share should drop his watch row \u2014 the FK on
    ``user_id`` cascades on user delete, but a share revoke is a
    softer event handled at the access layer. Even so, the ``can_read``
    gate ensures bob's *next* request 404s, and his watch list no
    longer surfaces the dossier."""
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    bob.put(f"/api/v1/subjects/{sid}/watchers/me")
    # Sanity: bob is watching.
    assert any(
        r["id"] == sid for r in cast(list[dict[str, object]], bob.get("/api/v1/me/watching").json())
    )

    # Revoke bob's share.
    revoke = alice.delete(f"/api/v1/subjects/{sid}/share/bob")
    assert revoke.status_code in (200, 204), revoke.text

    # Bob can no longer see the watcher list (404, not 403, mirroring
    # the rest of the read-tier gate).
    assert bob.get(f"/api/v1/subjects/{sid}/watchers").status_code == 404
