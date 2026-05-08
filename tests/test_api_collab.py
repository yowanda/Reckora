"""Phase 5 step 1 — comments + assignment per dossier.

Covers:

- Comment thread CRUD: create, list, delete (author / owner / admin paths).
- Permission model:
    * comments are visible to *any* reader (owner, sharer, assignee, admin);
    * unrelated viewers get 404 (don't leak existence).
* assignment management is owner / admin only; non-owners get 403.
- Assignment grants implicit read access (assignee can fetch the dossier
  and list comments without an explicit share).
- Idempotent assignment + audit trail (``assigned_by_username``).
- Cascade behaviour: deleting a subject removes its comments + assignments,
  deleting the assigning user collapses ``assigned_by`` to ``None`` rather
  than dropping the row.
- Validation: empty / whitespace-only comment bodies and oversize bodies
  are rejected.
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
    """Three viewer clients (alice, bob, carol) sharing one app.

    Three users let us cover the "outsider" path (carol) without
    having to register an admin in every assignment / comment test.
    """
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


# --- comments: happy path --------------------------------------------------


def test_owner_can_post_and_list_comments(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    posted = alice.post(
        f"/api/v1/subjects/{sid}/comments",
        json={"body": "Initial intake notes."},
    )
    assert posted.status_code == 201, posted.text
    body = posted.json()
    assert body["body"] == "Initial intake notes."
    assert body["author_username"] == "alice"
    assert body["updated_at"] is None
    assert isinstance(body["id"], int)
    assert body["created_at"]

    listed = alice.get(f"/api/v1/subjects/{sid}/comments")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["id"] == body["id"]
    assert rows[0]["body"] == "Initial intake notes."


def test_comments_are_oldest_first(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """The list endpoint must order by ``created_at`` ASC (with id as a
    deterministic tiebreaker for sub-millisecond inserts)."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    bodies = ["one", "two", "three", "four"]
    for body in bodies:
        r = alice.post(f"/api/v1/subjects/{sid}/comments", json={"body": body})
        assert r.status_code == 201
    rows = alice.get(f"/api/v1/subjects/{sid}/comments").json()
    assert [row["body"] for row in rows] == bodies


def test_sharer_can_comment(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Sharing the dossier with bob lets him both read AND comment.

    Comments require *read* access, not write — that's the whole point
    of using sharing as the collaboration primitive.
    """
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201

    posted = bob.post(
        f"/api/v1/subjects/{sid}/comments",
        json={"body": "Bob saw a phone-number lead."},
    )
    assert posted.status_code == 201
    assert posted.json()["author_username"] == "bob"

    rows = alice.get(f"/api/v1/subjects/{sid}/comments").json()
    assert {row["author_username"] for row in rows} == {"bob"}


def test_assignee_can_comment(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Being assigned (without an explicit share) must be enough to comment.

    Otherwise an operator could be tasked with a dossier they cannot
    annotate, which defeats the assignment workflow.
    """
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert (
        alice.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "bob"}).status_code == 201
    )

    posted = bob.post(
        f"/api/v1/subjects/{sid}/comments",
        json={"body": "Picked up by Bob."},
    )
    assert posted.status_code == 201


# --- comments: outsider isolation ------------------------------------------


def test_outsider_cannot_list_or_create_comments(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Carol has no access to alice's dossier — both read and write must 404
    (parity with the dossier-fetch endpoint, which also 404's to avoid
    leaking existence to outsiders).
    """
    alice, _bob, carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/comments", json={"body": "hello"}).status_code == 201

    assert carol.get(f"/api/v1/subjects/{sid}/comments").status_code == 404
    assert (
        carol.post(f"/api/v1/subjects/{sid}/comments", json={"body": "intruding"}).status_code
        == 404
    )


def test_comment_on_unknown_subject_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    assert alice.get("/api/v1/subjects/subj-missing/comments").status_code == 404
    assert (
        alice.post("/api/v1/subjects/subj-missing/comments", json={"body": "x"}).status_code == 404
    )


# --- comments: deletion authorisation --------------------------------------


def test_author_can_delete_own_comment(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    posted = bob.post(f"/api/v1/subjects/{sid}/comments", json={"body": "Bob's note."})
    cid = posted.json()["id"]
    deleted = bob.delete(f"/api/v1/subjects/{sid}/comments/{cid}")
    assert deleted.status_code == 204
    assert alice.get(f"/api/v1/subjects/{sid}/comments").json() == []


def test_owner_can_delete_any_comment(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    bob_comment = bob.post(f"/api/v1/subjects/{sid}/comments", json={"body": "Bob's note."}).json()
    deleted = alice.delete(f"/api/v1/subjects/{sid}/comments/{bob_comment['id']}")
    assert deleted.status_code == 204


def test_admin_can_delete_any_comment(
    client: TestClient,
    admin_token: str,
) -> None:
    _register(client, username="alice", password="alicepassword1")
    alice_token = _login(client, username="alice", password="alicepassword1")
    _set_auth(client, alice_token)
    sid = _create_subject(client)
    posted = client.post(f"/api/v1/subjects/{sid}/comments", json={"body": "alice."})
    cid = posted.json()["id"]

    _set_auth(client, admin_token)
    deleted = client.delete(f"/api/v1/subjects/{sid}/comments/{cid}")
    assert deleted.status_code == 204


def test_sharer_cannot_delete_owner_comment(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Sharers/assignees can read every comment but only delete their own —
    they can't erase the owner's audit trail.
    """
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    alice_comment = alice.post(
        f"/api/v1/subjects/{sid}/comments", json={"body": "Alice's note."}
    ).json()
    response = bob.delete(f"/api/v1/subjects/{sid}/comments/{alice_comment['id']}")
    assert response.status_code == 403


def test_outsider_delete_returns_404_not_403(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Outsiders that hit ``DELETE`` must get 404 — we already 404 the read
    path, so the delete path has to match or it leaks comment existence.
    """
    alice, _bob, carol = trio_clients
    sid = _create_subject(alice)
    cid = alice.post(f"/api/v1/subjects/{sid}/comments", json={"body": "alice"}).json()["id"]
    assert carol.delete(f"/api/v1/subjects/{sid}/comments/{cid}").status_code == 404


def test_delete_unknown_comment_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.delete(f"/api/v1/subjects/{sid}/comments/9999").status_code == 404


def test_delete_comment_wrong_subject_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Deleting a comment via a different subject's URL must 404 even if
    the actor is the owner of *that* other subject — comment ids are
    only meaningful in the context of their own subject.
    """
    alice, _bob, _carol = trio_clients
    sid_a = _create_subject(alice, value="alice")
    sid_b = _create_subject(alice, value="alice2")
    cid = alice.post(f"/api/v1/subjects/{sid_a}/comments", json={"body": "x"}).json()["id"]
    assert alice.delete(f"/api/v1/subjects/{sid_b}/comments/{cid}").status_code == 404


# --- comments: validation --------------------------------------------------


def test_empty_comment_body_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.post(f"/api/v1/subjects/{sid}/comments", json={"body": ""})
    assert response.status_code == 422


def test_whitespace_only_comment_body_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.post(
        f"/api/v1/subjects/{sid}/comments",
        json={"body": "   \t\n  "},
    )
    assert response.status_code == 422


def test_oversize_comment_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.post(
        f"/api/v1/subjects/{sid}/comments",
        json={"body": "x" * 10_001},
    )
    assert response.status_code == 422


def test_comment_extra_fields_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Extra fields in the body (e.g. an attempt to spoof an author) must
    be rejected — Pydantic's ``extra='forbid'`` is the contract."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.post(
        f"/api/v1/subjects/{sid}/comments",
        json={"body": "hi", "author_user_id": 999},
    )
    assert response.status_code == 422


# --- assignments: happy path ----------------------------------------------


def test_owner_can_assign_and_list(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.post(
        f"/api/v1/subjects/{sid}/assignees",
        json={"username": "bob"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["username"] == "bob"
    assert body["assigned_by_username"] == "alice"

    listed = alice.get(f"/api/v1/subjects/{sid}/assignees").json()
    assert [row["username"] for row in listed] == ["bob"]
    assert listed[0]["assigned_by_username"] == "alice"


def test_assignment_grants_read_access(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    # Before assignment Bob is locked out.
    assert bob.get(f"/api/v1/subjects/{sid}").status_code == 404
    # Assigning him grants read access.
    assert (
        alice.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "bob"}).status_code == 201
    )
    fetched = bob.get(f"/api/v1/subjects/{sid}")
    assert fetched.status_code == 200
    listed = bob.get("/api/v1/subjects").json()
    assert any(row["id"] == sid for row in listed)


def test_assignment_is_idempotent(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    first = alice.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "bob"})
    second = alice.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "bob"})
    assert first.status_code == 201
    assert second.status_code == 201
    # The original ``assigned_at`` wins so the audit trail is stable across
    # client retries on a flaky network.
    assert first.json()["assigned_at"] == second.json()["assigned_at"]
    listed = alice.get(f"/api/v1/subjects/{sid}/assignees").json()
    assert [row["username"] for row in listed] == ["bob"]


def test_owner_can_self_assign(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Owners may want to formally claim a dossier as the lead assignee
    (no implicit owner-as-assignee fallback exists, so this has to be
    an explicit add).
    """
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.post(
        f"/api/v1/subjects/{sid}/assignees",
        json={"username": "alice"},
    )
    assert response.status_code == 201
    assert response.json()["username"] == "alice"


def test_owner_can_revoke_assignment(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert (
        alice.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "bob"}).status_code == 201
    )
    assert bob.get(f"/api/v1/subjects/{sid}").status_code == 200

    revoke = alice.delete(f"/api/v1/subjects/{sid}/assignees/bob")
    assert revoke.status_code == 204
    assert alice.get(f"/api/v1/subjects/{sid}/assignees").json() == []
    # And Bob loses read access.
    assert bob.get(f"/api/v1/subjects/{sid}").status_code == 404


# --- assignments: outsider / non-owner mutations --------------------------


def test_non_owner_cannot_assign(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Sharers/assignees can read but not manage assignments — same policy
    as the share endpoints.
    """
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    # Share with bob so he becomes a *reader*, not a manager.
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201

    response = bob.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "carol"})
    assert response.status_code == 403
    response = bob.delete(f"/api/v1/subjects/{sid}/assignees/bob")
    assert response.status_code == 403


def test_outsider_cannot_list_assignees(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, carol = trio_clients
    sid = _create_subject(alice)
    response = carol.get(f"/api/v1/subjects/{sid}/assignees")
    assert response.status_code == 404


def test_sharer_can_list_assignees(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    assert (
        alice.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "bob"}).status_code == 201
    )
    listed = bob.get(f"/api/v1/subjects/{sid}/assignees")
    assert listed.status_code == 200
    assert [r["username"] for r in listed.json()] == ["bob"]


def test_assign_unknown_user_returns_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.post(
        f"/api/v1/subjects/{sid}/assignees",
        json={"username": "ghost"},
    )
    assert response.status_code == 404


def test_revoke_unassigned_user_returns_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.delete(f"/api/v1/subjects/{sid}/assignees/bob")
    assert response.status_code == 404


def test_revoke_unknown_user_returns_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.delete(f"/api/v1/subjects/{sid}/assignees/ghost")
    assert response.status_code == 404


# --- assignments: admin override ------------------------------------------


def test_admin_can_assign_on_any_subject(
    client: TestClient,
    admin_token: str,
) -> None:
    _register(client, username="alice", password="alicepassword1")
    _register(client, username="bob", password="bobpassword12")
    alice_token = _login(client, username="alice", password="alicepassword1")
    _set_auth(client, alice_token)
    sid = _create_subject(client)

    _set_auth(client, admin_token)
    response = client.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "bob"})
    assert response.status_code == 201
    assert response.json()["assigned_by_username"] == "root"

    bob_token = _login(client, username="bob", password="bobpassword12")
    _set_auth(client, bob_token)
    assert client.get(f"/api/v1/subjects/{sid}").status_code == 200


# --- assignments: validation ----------------------------------------------


def test_assignee_username_validation(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    too_short = alice.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "ab"})
    bad_char = alice.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "alice!"})
    extras = alice.post(
        f"/api/v1/subjects/{sid}/assignees",
        json={"username": "bob", "assigned_by": 1},
    )
    assert too_short.status_code == 422
    assert bad_char.status_code == 422
    assert extras.status_code == 422


# --- cascade behaviour -----------------------------------------------------


def test_deleting_subject_cascades_collab_rows(
    trio_clients: tuple[TestClient, TestClient, TestClient],
    api_settings: APISettings,
) -> None:
    """Deleting a subject must remove its comments and assignments.

    We probe the SQLite tables directly so we don't depend on any
    GET endpoint surviving the cascade.
    """
    import sqlite3

    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/comments", json={"body": "x"}).status_code == 201
    assert (
        alice.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "bob"}).status_code == 201
    )

    # Delete the dossier via the engine surface.
    deleted = alice.delete(f"/api/v1/subjects/{sid}")
    assert deleted.status_code == 204

    with sqlite3.connect(api_settings.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        c_count = conn.execute(
            "SELECT COUNT(*) FROM subject_comments WHERE subject_id = ?",
            (sid,),
        ).fetchone()[0]
        a_count = conn.execute(
            "SELECT COUNT(*) FROM subject_assignees WHERE subject_id = ?",
            (sid,),
        ).fetchone()[0]
    assert c_count == 0
    assert a_count == 0


def test_deleting_assigning_user_preserves_assignment(
    client: TestClient,
    admin_token: str,
    api_settings: APISettings,
) -> None:
    """``assigned_by`` is ``ON DELETE SET NULL`` — the assignment row
    survives, but the audit trail collapses to ``None``. We exercise it
    via the user-deactivation surface so the test mirrors the real flow.
    """
    _register(client, username="alice", password="alicepassword1")
    _register(client, username="bob", password="bobpassword12")
    alice_token = _login(client, username="alice", password="alicepassword1")
    _set_auth(client, alice_token)
    sid = _create_subject(client)
    assert (
        client.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "bob"}).status_code
        == 201
    )

    # Drop alice from the user table directly — there's no API surface
    # for hard-deleting users yet, but the cascade contract is what we
    # care about. We open a fresh sqlite3 connection (with FK pragma on)
    # rather than reusing UserRepository's, because that connection has
    # foreign keys disabled and ``ON DELETE SET NULL`` only fires when
    # FK enforcement is active on the connection issuing the DELETE.
    import sqlite3

    with UserRepository(api_settings.db_path) as repo:
        record = repo.get_by_username("alice")
        assert record is not None
    with sqlite3.connect(api_settings.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM users WHERE id = ?", (record.id,))

    _set_auth(client, admin_token)
    listed = client.get(f"/api/v1/subjects/{sid}/assignees").json()
    assert len(listed) == 1
    assert listed[0]["username"] == "bob"
    assert listed[0]["assigned_by_username"] is None
    assert listed[0]["assigned_by_user_id"] is None
