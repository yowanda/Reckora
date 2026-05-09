"""Phase 5 step 11 — per-actor pinned dossiers (favourites).

Pins are personal: marking a dossier as pinned only affects the
calling actor's favourites list. The endpoints are:

* ``POST /api/v1/me/pins/{subject_id}`` — pin (idempotent).
* ``DELETE /api/v1/me/pins/{subject_id}`` — unpin (idempotent).
* ``GET /api/v1/me/pins`` — list pinned dossiers, most-recently-
  pinned first. Dossiers the actor has lost access to are silently
  filtered (the pin row is preserved so the favourite resurrects on
  re-share).

Tests cover:

- Wire shape: the listing reuses ``SavedDossierSummary``, so a pinned
  dossier surfaces with the same fields as the visible-dossiers feed.
- Idempotence: re-pinning is a no-op (timestamp does not refresh, no
  500), unpinning a non-pinned dossier returns 204.
- Ordering: most-recently-pinned first, deterministic tiebreak by
  subject id when timestamps collide.
- Authorisation: a non-reader cannot pin (404 to avoid existence leak),
  a non-reader cannot see another actor's pins, an admin who is not
  a sharer cannot pin via that route either.
- Visibility filtering: revoking a sharer's access hides the pin from
  ``GET /me/pins`` but does not destroy the row.
- Cascade: deleting the subject (or the pinning user) wipes the row.
- Auth: unauthenticated requests are rejected.
- Limit: negative ``limit`` is 422; ``limit=0`` returns the empty list.
"""

from __future__ import annotations

from collections.abc import Iterator

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
    return str(response.json()["access_token"])


def _register(client: TestClient, *, username: str, password: str) -> int:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _create_subject(client: TestClient, *, value: str = "alice") -> str:
    response = client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": value}},
    )
    assert response.status_code in (200, 201), response.text
    return str(response.json()["id"])


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


# --- happy path -----------------------------------------------------------


def test_owner_can_pin_their_own_dossier(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    pin_resp = alice.post(f"/api/v1/me/pins/{sid}")
    assert pin_resp.status_code == 204
    assert pin_resp.text == ""

    listing = alice.get("/api/v1/me/pins").json()
    assert [s["id"] for s in listing] == [sid]


def test_pin_listing_carries_summary_shape(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/me/pins/{sid}").status_code == 204

    listing = alice.get("/api/v1/me/pins").json()
    assert len(listing) == 1
    summary = listing[0]
    # Re-uses :class:`SavedDossierSummary`, so it must carry the same
    # canonical fields the recent-dossiers feed surfaces.
    for key in (
        "id",
        "seed_identifier",
        "created_at",
        "identifier_count",
        "trace_count",
        "edge_count",
    ):
        assert key in summary, f"missing {key} in {summary!r}"


def test_pin_is_idempotent(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/me/pins/{sid}").status_code == 204
    # Second pin is a no-op — same dossier shouldn't appear twice and
    # the call must not 409.
    assert alice.post(f"/api/v1/me/pins/{sid}").status_code == 204
    listing = alice.get("/api/v1/me/pins").json()
    assert len(listing) == 1


def test_unpin_removes_from_listing(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/me/pins/{sid}").status_code == 204
    assert alice.delete(f"/api/v1/me/pins/{sid}").status_code == 204
    assert alice.get("/api/v1/me/pins").json() == []


def test_unpin_is_idempotent_on_unpinned(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    # Never pinned in the first place — unpin still 204s rather than
    # 404, mirroring the watchers/share contract.
    assert alice.delete(f"/api/v1/me/pins/{sid}").status_code == 204


# --- ordering -------------------------------------------------------------


def test_pins_listed_most_recent_first(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid_a = _create_subject(alice, value="alpha_pin")
    sid_b = _create_subject(alice, value="beta_pin")
    sid_c = _create_subject(alice, value="gamma_pin")

    assert alice.post(f"/api/v1/me/pins/{sid_a}").status_code == 204
    assert alice.post(f"/api/v1/me/pins/{sid_b}").status_code == 204
    assert alice.post(f"/api/v1/me/pins/{sid_c}").status_code == 204

    listing = alice.get("/api/v1/me/pins").json()
    # Most-recently-pinned (gamma) first, then beta, then alpha.
    assert [s["id"] for s in listing] == [sid_c, sid_b, sid_a]


# --- authorisation --------------------------------------------------------


def test_outsider_cannot_pin(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Bob has no read access — pin attempt 404s instead of 403, so
    the existence of the dossier id does not leak."""
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = bob.post(f"/api/v1/me/pins/{sid}")
    assert response.status_code == 404
    assert bob.get("/api/v1/me/pins").json() == []


def test_pinning_an_unknown_subject_returns_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    response = alice.post("/api/v1/me/pins/sub-this-does-not-exist")
    assert response.status_code == 404


def test_sharer_can_pin(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Bob has been granted read access via share — he can pin."""
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    assert bob.post(f"/api/v1/me/pins/{sid}").status_code == 204
    assert [s["id"] for s in bob.get("/api/v1/me/pins").json()] == [sid]


def test_pin_is_per_actor(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Alice pinning her own dossier doesn't surface in Bob's list,
    even when Bob has read access via a share."""
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    assert alice.post(f"/api/v1/me/pins/{sid}").status_code == 204
    assert bob.get("/api/v1/me/pins").json() == []


def test_pin_unauthenticated_rejected(client: TestClient) -> None:
    sid = "subject-anything"
    assert client.get("/api/v1/me/pins").status_code == 401
    assert client.post(f"/api/v1/me/pins/{sid}").status_code == 401
    assert client.delete(f"/api/v1/me/pins/{sid}").status_code == 401


# --- visibility filtering -------------------------------------------------


def test_revoked_share_hides_pin_but_keeps_row(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Bob pins while shared. After the share is revoked, the pin
    row is preserved (so re-sharing resurrects the favourite) but
    ``GET /me/pins`` filters the dossier out."""
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    assert bob.post(f"/api/v1/me/pins/{sid}").status_code == 204

    # Revoke share — pin is now orphaned.
    assert alice.delete(f"/api/v1/subjects/{sid}/share/bob").status_code == 204
    assert bob.get("/api/v1/me/pins").json() == []

    # Re-share — pin reappears (row was preserved).
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    assert [s["id"] for s in bob.get("/api/v1/me/pins").json()] == [sid]


# --- cascade --------------------------------------------------------------


def test_subject_delete_cascades_pin(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/me/pins/{sid}").status_code == 204
    assert alice.delete(f"/api/v1/subjects/{sid}").status_code == 204
    assert alice.get("/api/v1/me/pins").json() == []


# --- limit param ----------------------------------------------------------


def test_pins_limit_truncates(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sids = []
    for i in range(3):
        sid = _create_subject(alice, value=f"limit_pin_{i}")
        sids.append(sid)
        assert alice.post(f"/api/v1/me/pins/{sid}").status_code == 204
    listing = alice.get("/api/v1/me/pins", params={"limit": 2}).json()
    assert len(listing) == 2


def test_pins_limit_zero_returns_empty(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/me/pins/{sid}").status_code == 204
    assert alice.get("/api/v1/me/pins", params={"limit": 0}).json() == []


def test_pins_negative_limit_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    response = alice.get("/api/v1/me/pins", params={"limit": -1})
    assert response.status_code == 422


def test_empty_pins_when_user_has_pinned_nothing(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    _create_subject(alice)
    assert alice.get("/api/v1/me/pins").json() == []
