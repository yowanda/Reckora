"""Phase 5 step 4 — comment editing.

Authorisation model
-------------------

Comment editing is intentionally tighter than comment deletion:

* The **author** can always edit their own comment, even after losing
  read access to the dossier (e.g. their share was revoked). Being on
  the hook for your own past words is independent of the access window.
* Nobody else can edit — not the dossier owner, not an admin, not
  another sharer or assignee. Letting somebody else rewrite a comment
  would attribute new words to a third party, which is a worse audit
  property than letting them ``DELETE`` it (delete just removes the
  row; nobody is left misquoted).
* Outsiders get 404 to mirror the rest of the API surface.

Tests
-----

We cover the happy path, the negative-auth permutations (owner / admin
/ sharer / assignee / outsider), the cross-subject 404, validation
(empty / whitespace / oversize / extra fields), and the side-effect
that ``updated_at`` flips to a populated value while ``created_at``
stays anchored.
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


def _post_comment(client: TestClient, sid: str, body: str = "Initial.") -> dict[str, object]:
    response = client.post(f"/api/v1/subjects/{sid}/comments", json={"body": body})
    assert response.status_code == 201, response.text
    out: dict[str, object] = response.json()
    return out


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


def test_author_can_edit_own_comment(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    posted = _post_comment(alice, sid, "First take.")

    response = alice.patch(
        f"/api/v1/subjects/{sid}/comments/{posted['id']}",
        json={"body": "Refined take."},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == posted["id"]
    assert body["body"] == "Refined take."
    assert body["author_username"] == "alice"
    # ``updated_at`` flips to a populated value, while ``created_at``
    # remains the original anchor — clients use the gap to render an
    # "(edited)" badge.
    assert body["created_at"] == posted["created_at"]
    assert body["updated_at"] is not None
    assert body["updated_at"] != body["created_at"]


def test_edit_persists_in_subsequent_list(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    posted = _post_comment(alice, sid, "Original.")
    alice.patch(
        f"/api/v1/subjects/{sid}/comments/{posted['id']}",
        json={"body": "Edited."},
    )
    rows = alice.get(f"/api/v1/subjects/{sid}/comments").json()
    assert len(rows) == 1
    assert rows[0]["body"] == "Edited."
    assert rows[0]["updated_at"] is not None


def test_author_can_edit_after_losing_read_access(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Bob authors a comment on alice's dossier (via share). Alice
    revokes the share. Bob should still be able to edit his own
    comment — being on the hook for one's own past words trumps the
    access window."""
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    posted = _post_comment(bob, sid, "Bob's note.")

    # Alice revokes bob's share.
    assert alice.delete(f"/api/v1/subjects/{sid}/share/bob").status_code == 204

    # Bob can no longer list comments…
    assert bob.get(f"/api/v1/subjects/{sid}/comments").status_code == 404

    # …but he can still edit his own.
    response = bob.patch(
        f"/api/v1/subjects/{sid}/comments/{posted['id']}",
        json={"body": "Updated by Bob."},
    )
    assert response.status_code == 200
    assert response.json()["body"] == "Updated by Bob."


# --- author-only authorisation --------------------------------------------


def test_owner_cannot_edit_other_users_comment(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Even the dossier owner cannot rewrite somebody else's words."""
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    bob_comment = _post_comment(bob, sid, "Bob's note.")

    response = alice.patch(
        f"/api/v1/subjects/{sid}/comments/{bob_comment['id']}",
        json={"body": "Alice rewrote this."},
    )
    assert response.status_code == 403
    # Body must NOT have been mutated.
    rows = alice.get(f"/api/v1/subjects/{sid}/comments").json()
    assert rows[0]["body"] == "Bob's note."
    assert rows[0]["updated_at"] is None


def test_admin_cannot_edit_other_users_comment(
    client: TestClient,
    admin_token: str,
) -> None:
    """Admins can delete any comment but cannot edit one — same
    audit-integrity argument as the owner case."""
    _register(client, username="alice", password="alicepassword1")
    alice_token = _login(client, username="alice", password="alicepassword1")
    _set_auth(client, alice_token)
    sid = _create_subject(client)
    posted = _post_comment(client, sid, "Alice's note.")

    _set_auth(client, admin_token)
    response = client.patch(
        f"/api/v1/subjects/{sid}/comments/{posted['id']}",
        json={"body": "Admin override."},
    )
    assert response.status_code == 403


def test_sharer_cannot_edit_other_users_comment(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    alice_comment = _post_comment(alice, sid, "Alice's note.")

    response = bob.patch(
        f"/api/v1/subjects/{sid}/comments/{alice_comment['id']}",
        json={"body": "Bob rewrote this."},
    )
    assert response.status_code == 403


def test_assignee_cannot_edit_other_users_comment(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert (
        alice.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "bob"}).status_code == 201
    )
    alice_comment = _post_comment(alice, sid, "Alice's note.")

    response = bob.patch(
        f"/api/v1/subjects/{sid}/comments/{alice_comment['id']}",
        json={"body": "Bob (assignee) rewrote this."},
    )
    assert response.status_code == 403


def test_outsider_edit_returns_404_not_403(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Carol has no access — same 404 as the read path so we don't
    leak comment existence to outsiders."""
    alice, _bob, carol = trio_clients
    sid = _create_subject(alice)
    posted = _post_comment(alice, sid, "Alice's note.")

    response = carol.patch(
        f"/api/v1/subjects/{sid}/comments/{posted['id']}",
        json={"body": "Carol's edit."},
    )
    assert response.status_code == 404


# --- 404 paths -------------------------------------------------------------


def test_edit_unknown_comment_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.patch(
        f"/api/v1/subjects/{sid}/comments/99999",
        json={"body": "edit"},
    )
    assert response.status_code == 404


def test_edit_unknown_subject_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    response = alice.patch(
        "/api/v1/subjects/subj-missing/comments/1",
        json={"body": "edit"},
    )
    assert response.status_code == 404


def test_edit_comment_wrong_subject_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Crossing a comment id from one subject onto a different
    subject's URL must 404 — the comment id is global but the
    surface is namespaced under ``/subjects/{sid}/comments/``."""
    alice, _bob, _carol = trio_clients
    sid_a = _create_subject(alice, value="alice")
    sid_b = _create_subject(alice, value="bob")
    posted = _post_comment(alice, sid_a, "On dossier A.")

    response = alice.patch(
        f"/api/v1/subjects/{sid_b}/comments/{posted['id']}",
        json={"body": "Edit through wrong sid."},
    )
    assert response.status_code == 404


# --- validation ------------------------------------------------------------


def test_empty_body_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    posted = _post_comment(alice, sid, "Original.")
    response = alice.patch(
        f"/api/v1/subjects/{sid}/comments/{posted['id']}",
        json={"body": ""},
    )
    assert response.status_code == 422


def test_whitespace_only_body_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Pydantic's ``min_length=1`` doesn't catch whitespace-only
    payloads — the route does an additional ``.strip()`` check that
    must reject them."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    posted = _post_comment(alice, sid, "Original.")
    response = alice.patch(
        f"/api/v1/subjects/{sid}/comments/{posted['id']}",
        json={"body": "   \n\t  "},
    )
    assert response.status_code == 422


def test_oversize_body_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    posted = _post_comment(alice, sid, "Original.")
    response = alice.patch(
        f"/api/v1/subjects/{sid}/comments/{posted['id']}",
        json={"body": "x" * 10_001},
    )
    assert response.status_code == 422


def test_extra_fields_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """``CommentUpdate`` uses ``extra='forbid'`` — clients trying to
    sneak ``id`` / ``author_user_id`` / ``created_at`` overrides
    through the PATCH must get rejected."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    posted = _post_comment(alice, sid, "Original.")
    response = alice.patch(
        f"/api/v1/subjects/{sid}/comments/{posted['id']}",
        json={"body": "edit", "author_user_id": 999},
    )
    assert response.status_code == 422


# --- side effects ----------------------------------------------------------


def test_repeated_edit_updates_timestamp(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Successive PATCHes must keep advancing ``updated_at`` while
    leaving ``created_at`` anchored. We use one-second-resolution
    timestamps so we just assert the second call's body is what
    sticks."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    posted = _post_comment(alice, sid, "v1")

    first = alice.patch(
        f"/api/v1/subjects/{sid}/comments/{posted['id']}",
        json={"body": "v2"},
    ).json()
    second = alice.patch(
        f"/api/v1/subjects/{sid}/comments/{posted['id']}",
        json={"body": "v3"},
    ).json()
    assert first["body"] == "v2"
    assert second["body"] == "v3"
    assert second["created_at"] == posted["created_at"]
    assert second["updated_at"] is not None
