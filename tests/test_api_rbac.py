"""Phase 5 — RBAC + subject ownership end-to-end behaviour.

Covers:

- Default registration mints viewer-role users; ``/auth/me`` exposes the role.
- Subjects are owner-scoped: viewer A cannot see / fetch / delete viewer B's
  dossiers and gets 404 (not 403) so the API doesn't leak existence.
- Admins see and can manage every dossier, including legacy un-owned rows.
- Sharing endpoint round-trip: owner shares with another viewer, shared
  viewer can list and fetch the dossier but cannot delete it; owner revokes
  the share and access disappears.
- ``GET /users`` and ``PATCH /users/{id}/role`` are admin-only; admins cannot
  demote themselves.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from reckora.persistence.sqlite import SQLiteSubjectRepository
from reckora_api.auth.models import Role
from reckora_api.auth.passwords import hash_password
from reckora_api.auth.repository import UserRepository
from reckora_api.config import APISettings

# Each call to ``client.post(...)`` etc. on a TestClient yields a real
# ``httpx.Response``; the JSON shapes are checked at runtime so we cast
# inline to keep the test code terse.


# --- fixtures --------------------------------------------------------------


def _login(client: TestClient, *, username: str, password: str) -> str:
    """Issue a token via the OAuth2 password grant."""
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


@pytest.fixture
def admin_token(client: TestClient, api_settings: APISettings) -> str:
    """Bootstrap an admin user via the CLI surface (UserRepository)."""
    with UserRepository(api_settings.db_path) as repo:
        repo.create_user(
            username="root",
            password_hash=hash_password("rootsecret123"),
            role=Role.ADMIN,
        )
    return _login(client, username="root", password="rootsecret123")


@pytest.fixture
def alice_client(client: TestClient) -> TestClient:
    """A fresh viewer client for ``alice``."""
    _register(client, username="alice", password="alicepassword1")
    token = _login(client, username="alice", password="alicepassword1")
    _set_auth(client, token)
    return client


@pytest.fixture
def two_viewer_clients(client: TestClient) -> Iterator[tuple[TestClient, TestClient]]:
    """A second :class:`TestClient` against the same app, so two viewers can
    hit the API concurrently with independent ``Authorization`` headers.
    """
    _register(client, username="alice", password="alicepassword1")
    _register(client, username="bob", password="bobpassword12")

    alice_token = _login(client, username="alice", password="alicepassword1")
    bob_token = _login(client, username="bob", password="bobpassword12")

    alice_app = TestClient(client.app)
    alice_app.headers["Authorization"] = f"Bearer {alice_token}"
    bob_app = TestClient(client.app)
    bob_app.headers["Authorization"] = f"Bearer {bob_token}"

    try:
        yield alice_app, bob_app
    finally:
        alice_app.close()
        bob_app.close()


# --- registration / role surfacing -----------------------------------------


def test_register_defaults_to_viewer_role(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "alicepassword1"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "viewer"


def test_me_exposes_role(alice_client: TestClient) -> None:
    body = alice_client.get("/api/v1/auth/me").json()
    assert body["username"] == "alice"
    assert body["role"] == "viewer"


# --- ownership scoping on /subjects ----------------------------------------


def test_viewer_only_sees_their_own_subjects(
    two_viewer_clients: tuple[TestClient, TestClient],
) -> None:
    alice, bob = two_viewer_clients
    a = alice.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()
    b = bob.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "bob"}},
    ).json()

    alice_list = alice.get("/api/v1/subjects").json()
    bob_list = bob.get("/api/v1/subjects").json()

    assert {row["id"] for row in alice_list} == {a["id"]}
    assert {row["id"] for row in bob_list} == {b["id"]}
    assert alice_list[0]["owner_username"] == "alice"
    assert bob_list[0]["owner_username"] == "bob"


def test_viewer_cannot_get_other_users_subject(
    two_viewer_clients: tuple[TestClient, TestClient],
) -> None:
    alice, bob = two_viewer_clients
    bob_subject = bob.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "bob"}},
    ).json()
    response = alice.get(f"/api/v1/subjects/{bob_subject['id']}")
    assert response.status_code == 404


def test_viewer_cannot_delete_other_users_subject(
    two_viewer_clients: tuple[TestClient, TestClient],
) -> None:
    alice, bob = two_viewer_clients
    bob_subject = bob.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "bob"}},
    ).json()
    response = alice.delete(f"/api/v1/subjects/{bob_subject['id']}")
    # 404 (not 403) — alice has no read access either, so we don't leak existence.
    assert response.status_code == 404
    # And the dossier is still around for bob.
    follow = bob.get(f"/api/v1/subjects/{bob_subject['id']}")
    assert follow.status_code == 200


def test_viewer_cannot_render_other_users_dossier(
    two_viewer_clients: tuple[TestClient, TestClient],
) -> None:
    alice, bob = two_viewer_clients
    bob_subject = bob.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "bob"}},
    ).json()
    response = alice.get(
        f"/api/v1/subjects/{bob_subject['id']}/dossier",
        params={"format": "json"},
    )
    assert response.status_code == 404


# --- admin override --------------------------------------------------------


def test_admin_lists_all_subjects_including_others(
    client: TestClient,
    admin_token: str,
) -> None:
    _register(client, username="alice", password="alicepassword1")
    alice_token = _login(client, username="alice", password="alicepassword1")
    _set_auth(client, alice_token)
    alice_subject = client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()

    _set_auth(client, admin_token)
    rows = client.get("/api/v1/subjects").json()
    assert any(row["id"] == alice_subject["id"] for row in rows)


def test_admin_can_get_and_delete_any_subject(
    client: TestClient,
    admin_token: str,
) -> None:
    _register(client, username="alice", password="alicepassword1")
    alice_token = _login(client, username="alice", password="alicepassword1")
    _set_auth(client, alice_token)
    alice_subject = client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()

    _set_auth(client, admin_token)
    fetched = client.get(f"/api/v1/subjects/{alice_subject['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["owner_username"] == "alice"
    deleted = client.delete(f"/api/v1/subjects/{alice_subject['id']}")
    assert deleted.status_code == 204


def test_admin_sees_legacy_unowned_subjects(
    client: TestClient,
    admin_token: str,
    api_settings: APISettings,
) -> None:
    """A subject inserted directly via :class:`SQLiteSubjectRepository`
    (mirroring the CLI's ``reckora investigate --save`` path) has no
    entry in ``subject_owners``; admins must still see and manage it.
    """
    from datetime import UTC, datetime

    from reckora.evidence.chain import make_evidence
    from reckora.models.entity import Identifier, Subject, Trace
    from reckora.models.enums import IdentifierType, TraceSource

    seed = Identifier(type=IdentifierType.USERNAME, value="orphan")
    trace = Trace(
        identifier=seed,
        source=TraceSource.WEB_PROFILE,
        fields={"platform": "fake", "display_name": "orphan"},
        evidence=make_evidence(
            "https://fake.example.com/orphan",
            {"login": "orphan"},
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    subject = Subject(
        id="subj-orphan",
        seed_identifier=seed,
        identifiers=[seed],
        traces=[trace],
    )
    with SQLiteSubjectRepository(api_settings.db_path) as repo:
        repo.save(subject=subject, traces=[trace], edges=[])

    _set_auth(client, admin_token)
    rows = client.get("/api/v1/subjects").json()
    found = next(row for row in rows if row["id"] == "subj-orphan")
    assert found["owner_username"] is None

    fetched = client.get("/api/v1/subjects/subj-orphan")
    assert fetched.status_code == 200
    assert fetched.json()["owner_username"] is None


def test_viewer_does_not_see_legacy_unowned_subjects(
    two_viewer_clients: tuple[TestClient, TestClient],
    api_settings: APISettings,
) -> None:
    """The mirror of the admin case: legacy un-owned rows must NOT leak
    into a viewer's listing or be fetchable by a viewer.
    """
    from datetime import UTC, datetime

    from reckora.evidence.chain import make_evidence
    from reckora.models.entity import Identifier, Subject, Trace
    from reckora.models.enums import IdentifierType, TraceSource

    seed = Identifier(type=IdentifierType.USERNAME, value="orphan")
    trace = Trace(
        identifier=seed,
        source=TraceSource.WEB_PROFILE,
        fields={"platform": "fake", "display_name": "orphan"},
        evidence=make_evidence(
            "https://fake.example.com/orphan",
            {"login": "orphan"},
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    subject = Subject(
        id="subj-orphan",
        seed_identifier=seed,
        identifiers=[seed],
        traces=[trace],
    )
    with SQLiteSubjectRepository(api_settings.db_path) as repo:
        repo.save(subject=subject, traces=[trace], edges=[])

    alice, _ = two_viewer_clients
    rows = alice.get("/api/v1/subjects").json()
    assert all(row["id"] != "subj-orphan" for row in rows)
    assert alice.get("/api/v1/subjects/subj-orphan").status_code == 404


# --- sharing round-trip ----------------------------------------------------


def test_owner_can_share_and_revoke(
    two_viewer_clients: tuple[TestClient, TestClient],
) -> None:
    alice, bob = two_viewer_clients
    alice_subject = alice.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()

    # Bob can't see it yet.
    assert bob.get(f"/api/v1/subjects/{alice_subject['id']}").status_code == 404

    # Alice shares with Bob.
    share = alice.post(
        f"/api/v1/subjects/{alice_subject['id']}/share",
        json={"username": "bob"},
    )
    assert share.status_code == 201, share.text
    assert share.json()["username"] == "bob"

    # Bob now sees it in /subjects and can fetch it.
    listed = bob.get("/api/v1/subjects").json()
    assert any(row["id"] == alice_subject["id"] for row in listed)
    fetched = bob.get(f"/api/v1/subjects/{alice_subject['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["owner_username"] == "alice"

    # ...but Bob can't delete it.
    assert bob.delete(f"/api/v1/subjects/{alice_subject['id']}").status_code == 403

    # Alice revokes.
    revoke = alice.delete(
        f"/api/v1/subjects/{alice_subject['id']}/share/bob",
    )
    assert revoke.status_code == 204

    # Bob loses access.
    assert bob.get(f"/api/v1/subjects/{alice_subject['id']}").status_code == 404
    assert all(
        row["id"] != alice_subject["id"]
        for row in bob.get("/api/v1/subjects").json()
    )


def test_share_idempotent(
    two_viewer_clients: tuple[TestClient, TestClient],
) -> None:
    alice, _bob = two_viewer_clients
    s = alice.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()
    first = alice.post(f"/api/v1/subjects/{s['id']}/share", json={"username": "bob"})
    second = alice.post(f"/api/v1/subjects/{s['id']}/share", json={"username": "bob"})
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["created_at"] == second.json()["created_at"]


def test_share_rejects_self(
    alice_client: TestClient,
) -> None:
    s = alice_client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()
    response = alice_client.post(
        f"/api/v1/subjects/{s['id']}/share",
        json={"username": "alice"},
    )
    assert response.status_code == 409


def test_non_owner_cannot_manage_shares(
    two_viewer_clients: tuple[TestClient, TestClient],
) -> None:
    alice, bob = two_viewer_clients
    alice_subject = alice.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()

    # Non-owners get 403 from the share endpoints. The dossier-existence
    # leak is acceptable here because the share endpoint is gated behind
    # an owner-or-admin check; viewers never reach it through normal
    # navigation.
    assert bob.get(f"/api/v1/subjects/{alice_subject['id']}/share").status_code == 403
    create = bob.post(
        f"/api/v1/subjects/{alice_subject['id']}/share",
        json={"username": "bob"},
    )
    assert create.status_code == 403
    revoke = bob.delete(f"/api/v1/subjects/{alice_subject['id']}/share/bob")
    assert revoke.status_code == 403


def test_share_to_unknown_user_returns_404(
    alice_client: TestClient,
) -> None:
    s = alice_client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()
    response = alice_client.post(
        f"/api/v1/subjects/{s['id']}/share",
        json={"username": "ghost"},
    )
    assert response.status_code == 404


def test_revoke_unshared_returns_404(
    two_viewer_clients: tuple[TestClient, TestClient],
) -> None:
    alice, _ = two_viewer_clients
    s = alice.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()
    response = alice.delete(f"/api/v1/subjects/{s['id']}/share/bob")
    assert response.status_code == 404


def test_admin_can_manage_shares_on_any_subject(
    client: TestClient,
    admin_token: str,
) -> None:
    _register(client, username="alice", password="alicepassword1")
    _register(client, username="bob", password="bobpassword12")
    alice_token = _login(client, username="alice", password="alicepassword1")
    _set_auth(client, alice_token)
    alice_subject = client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()

    _set_auth(client, admin_token)
    response = client.post(
        f"/api/v1/subjects/{alice_subject['id']}/share",
        json={"username": "bob"},
    )
    assert response.status_code == 201

    # Bob now has access via the admin-driven share.
    bob_token = _login(client, username="bob", password="bobpassword12")
    _set_auth(client, bob_token)
    fetched = client.get(f"/api/v1/subjects/{alice_subject['id']}")
    assert fetched.status_code == 200


# --- user management endpoints --------------------------------------------


def test_users_list_requires_admin(alice_client: TestClient) -> None:
    response = alice_client.get("/api/v1/users")
    assert response.status_code == 403


def test_users_list_returns_all_users(
    client: TestClient,
    admin_token: str,
) -> None:
    _register(client, username="alice", password="alicepassword1")
    _register(client, username="bob", password="bobpassword12")
    _set_auth(client, admin_token)
    rows = client.get("/api/v1/users").json()
    usernames = {row["username"] for row in rows}
    assert {"root", "alice", "bob"}.issubset(usernames)
    by_user = {row["username"]: row for row in rows}
    assert by_user["root"]["role"] == "admin"
    assert by_user["alice"]["role"] == "viewer"


def test_admin_can_promote_and_demote(
    client: TestClient,
    admin_token: str,
) -> None:
    alice_id = _register(client, username="alice", password="alicepassword1")
    _set_auth(client, admin_token)
    promoted = client.patch(
        f"/api/v1/users/{alice_id}/role",
        json={"role": "admin"},
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["role"] == "admin"

    demoted = client.patch(
        f"/api/v1/users/{alice_id}/role",
        json={"role": "viewer"},
    )
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "viewer"


def test_admin_cannot_demote_self(
    client: TestClient,
    admin_token: str,
) -> None:
    _set_auth(client, admin_token)
    me = client.get("/api/v1/auth/me").json()
    response = client.patch(
        f"/api/v1/users/{me['id']}/role",
        json={"role": "viewer"},
    )
    assert response.status_code == 409


def test_promote_unknown_user_returns_404(
    client: TestClient,
    admin_token: str,
) -> None:
    _set_auth(client, admin_token)
    response = client.patch(
        "/api/v1/users/9999/role",
        json={"role": "admin"},
    )
    assert response.status_code == 404


def test_promoted_user_immediately_sees_admin_powers(
    client: TestClient,
    admin_token: str,
) -> None:
    """Role lookups happen on every request — promotion takes effect with
    the *existing* token, no re-login needed.
    """
    alice_id = _register(client, username="alice", password="alicepassword1")
    alice_token = _login(client, username="alice", password="alicepassword1")
    # Before promotion: no /users access.
    _set_auth(client, alice_token)
    assert client.get("/api/v1/users").status_code == 403

    _set_auth(client, admin_token)
    client.patch(f"/api/v1/users/{alice_id}/role", json={"role": "admin"})

    _set_auth(client, alice_token)  # same token as before
    assert client.get("/api/v1/users").status_code == 200


# --- migration semantics --------------------------------------------------


def test_legacy_users_table_grants_admin_on_migration(tmp_path: object) -> None:
    """A users table that predates Phase 5 (no ``role`` column) must have
    ``role='admin'`` after ``UserRepository.__init__`` runs.

    We hand-roll the legacy schema with ``sqlite3`` directly to simulate a
    pre-RBAC database, then open it through :class:`UserRepository` and
    assert the existing user lands as an admin.
    """
    import sqlite3
    from datetime import UTC, datetime
    from pathlib import Path

    db_path = Path(str(tmp_path)) / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    conn.execute(
        "INSERT INTO users(username, password_hash, created_at, is_active)"
        " VALUES (?, ?, ?, 1)",
        ("legacy", hash_password("legacypassword1"), datetime.now(UTC).isoformat()),
    )
    conn.commit()
    conn.close()

    with UserRepository(str(db_path)) as repo:
        record = repo.get_by_username("legacy")
        assert record is not None
        assert record.role is Role.ADMIN
