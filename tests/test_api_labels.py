"""Phase 5 step 6 — per-dossier labels (free-form tags).

Covers:

- Per-dossier CRUD: PUT (idempotent) / DELETE / GET.
- Label normalisation: input is trimmed + lower-cased, regex-validated.
- Authorisation:
    * Read: any reader (owner / sharer / assignee / admin); outsider 404.
    * Write: dossier owner or admin only — sharers / assignees get 403.
- Catalog endpoint at ``/api/v1/labels`` returns label-with-count
  rows scoped to the actor's visibility (admins see global counts).
- Cascade: deleting a subject removes its label rows.
- Pattern validation: rejects empty / whitespace, invalid characters,
  oversize labels.
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


def _put_label(client: TestClient, sid: str, label: str) -> dict[str, object]:
    response = client.put(f"/api/v1/subjects/{sid}/labels/{label}")
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


# --- happy path ------------------------------------------------------------


def test_owner_can_add_and_list_labels(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    put = alice.put(f"/api/v1/subjects/{sid}/labels/osint")
    assert put.status_code == 200, put.text
    body = put.json()
    assert [row["label"] for row in body] == ["osint"]
    assert body[0]["created_by"] == "alice"
    assert body[0]["created_at"]

    listing = alice.get(f"/api/v1/subjects/{sid}/labels")
    assert listing.status_code == 200, listing.text
    assert [row["label"] for row in listing.json()] == ["osint"]


def test_labels_alphabetically_sorted(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    for label in ["zebra", "alpha", "mike"]:
        assert alice.put(f"/api/v1/subjects/{sid}/labels/{label}").status_code == 200

    listing = alice.get(f"/api/v1/subjects/{sid}/labels")
    assert [row["label"] for row in listing.json()] == ["alpha", "mike", "zebra"]


def test_put_label_is_idempotent(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    first = alice.put(f"/api/v1/subjects/{sid}/labels/osint")
    second = alice.put(f"/api/v1/subjects/{sid}/labels/osint")

    assert first.status_code == 200
    assert second.status_code == 200
    assert [row["label"] for row in second.json()] == ["osint"]


def test_owner_can_remove_label(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    alice.put(f"/api/v1/subjects/{sid}/labels/osint")
    alice.put(f"/api/v1/subjects/{sid}/labels/threat")

    removed = alice.delete(f"/api/v1/subjects/{sid}/labels/osint")
    assert removed.status_code == 200, removed.text
    assert [row["label"] for row in removed.json()] == ["threat"]


def test_remove_unknown_label_returns_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    response = alice.delete(f"/api/v1/subjects/{sid}/labels/never-applied")
    assert response.status_code == 404


# --- normalisation ---------------------------------------------------------


def test_label_is_lowercased(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """The route should canonicalise ``OSINT`` → ``osint`` so it can be
    deduped against an already-applied lower-case copy."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    alice.put(f"/api/v1/subjects/{sid}/labels/osint")
    second = alice.put(f"/api/v1/subjects/{sid}/labels/OSINT")

    assert second.status_code == 200
    assert [row["label"] for row in second.json()] == ["osint"]


@pytest.mark.parametrize(
    "bad_label",
    [
        "_starts_underscore",
        "-starts-dash",
        ".starts.dot",
        "has@at",
        "has!bang",
        "has(paren",
        "x" * 33,
    ],
)
def test_invalid_label_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
    bad_label: str,
) -> None:
    """Bad punctuation / oversize labels are rejected with 422.

    We deliberately avoid characters that confuse URL parsing
    (whitespace, ``?``, ``#``, ``/``, backslash) — those are tested
    via direct path-constraint coverage from FastAPI itself.
    """
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    response = alice.put(f"/api/v1/subjects/{sid}/labels/{bad_label}")
    assert response.status_code == 422, response.text


def test_label_strips_surrounding_whitespace(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    response = alice.put(f"/api/v1/subjects/{sid}/labels/%20osint%20")
    assert response.status_code == 200
    assert [row["label"] for row in response.json()] == ["osint"]


# --- authorisation ---------------------------------------------------------


def test_sharer_can_read_but_not_write(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)

    alice.put(f"/api/v1/subjects/{sid}/labels/osint")

    share = alice.post(
        f"/api/v1/subjects/{sid}/share",
        json={"username": "bob"},
    )
    assert share.status_code in (200, 201), share.text

    listing = bob.get(f"/api/v1/subjects/{sid}/labels")
    assert listing.status_code == 200
    assert [row["label"] for row in listing.json()] == ["osint"]

    write = bob.put(f"/api/v1/subjects/{sid}/labels/threat")
    assert write.status_code == 403


def test_assignee_can_read_but_not_write(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)

    alice.put(f"/api/v1/subjects/{sid}/labels/osint")
    assigned = alice.post(
        f"/api/v1/subjects/{sid}/assignees",
        json={"username": "bob"},
    )
    assert assigned.status_code in (200, 201), assigned.text

    listing = bob.get(f"/api/v1/subjects/{sid}/labels")
    assert listing.status_code == 200
    assert [row["label"] for row in listing.json()] == ["osint"]

    write = bob.put(f"/api/v1/subjects/{sid}/labels/threat")
    assert write.status_code == 403


def test_outsider_gets_404_not_403(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Outsiders shouldn't be able to distinguish 'no such subject'
    from 'not allowed' — both return 404."""
    alice, _bob, carol = trio_clients
    sid = _create_subject(alice)

    listing = carol.get(f"/api/v1/subjects/{sid}/labels")
    assert listing.status_code == 404

    write = carol.put(f"/api/v1/subjects/{sid}/labels/osint")
    assert write.status_code == 404

    delete = carol.delete(f"/api/v1/subjects/{sid}/labels/osint")
    assert delete.status_code == 404


def test_admin_can_label_any_subject(
    client: TestClient,
    trio_clients: tuple[TestClient, TestClient, TestClient],
    admin_token: str,
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    admin = TestClient(client.app)
    admin.headers["Authorization"] = f"Bearer {admin_token}"

    response = admin.put(f"/api/v1/subjects/{sid}/labels/admin-tagged")
    assert response.status_code == 200, response.text
    admin.close()


def test_unknown_subject_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    response = alice.put("/api/v1/subjects/subj-does-not-exist/labels/osint")
    assert response.status_code == 404


def test_unauthenticated_request_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/subjects/subj-anything/labels")
    assert response.status_code == 401


# --- global catalog --------------------------------------------------------


def test_catalog_aggregates_visible_labels(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid_one = _create_subject(alice, value="alice")
    sid_two = _create_subject(alice, value="bob")

    alice.put(f"/api/v1/subjects/{sid_one}/labels/osint")
    alice.put(f"/api/v1/subjects/{sid_one}/labels/threat")
    alice.put(f"/api/v1/subjects/{sid_two}/labels/osint")

    catalog = alice.get("/api/v1/labels")
    assert catalog.status_code == 200, catalog.text
    rows = catalog.json()
    # Sorted by descending count, then label.
    assert rows == [
        {"label": "osint", "count": 2},
        {"label": "threat", "count": 1},
    ]


def test_catalog_scoped_to_actor_visibility(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Carol can't see alice's tags until alice shares with her."""
    alice, _bob, carol = trio_clients
    sid = _create_subject(alice)
    alice.put(f"/api/v1/subjects/{sid}/labels/osint")

    # Carol initially sees nothing.
    initial = carol.get("/api/v1/labels")
    assert initial.status_code == 200
    assert initial.json() == []

    # After sharing, the label appears in carol's catalog.
    alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "carol"})

    after = carol.get("/api/v1/labels")
    assert after.status_code == 200
    assert after.json() == [{"label": "osint", "count": 1}]


def test_admin_catalog_is_global(
    client: TestClient,
    trio_clients: tuple[TestClient, TestClient, TestClient],
    admin_token: str,
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    alice.put(f"/api/v1/subjects/{sid}/labels/osint")

    admin = TestClient(client.app)
    admin.headers["Authorization"] = f"Bearer {admin_token}"
    catalog = admin.get("/api/v1/labels")
    assert catalog.status_code == 200
    assert catalog.json() == [{"label": "osint", "count": 1}]
    admin.close()


# --- cascades --------------------------------------------------------------


def test_deleting_subject_removes_its_labels(
    client: TestClient,
    trio_clients: tuple[TestClient, TestClient, TestClient],
    admin_token: str,
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    alice.put(f"/api/v1/subjects/{sid}/labels/osint")

    admin = TestClient(client.app)
    admin.headers["Authorization"] = f"Bearer {admin_token}"
    deleted = admin.delete(f"/api/v1/subjects/{sid}")
    assert deleted.status_code in (200, 204), deleted.text
    admin.close()

    catalog = alice.get("/api/v1/labels")
    assert catalog.status_code == 200
    assert catalog.json() == []
