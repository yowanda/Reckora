"""Phase 5 step 9 — comment threading (one-level replies).

Replies extend the comments surface with a ``parent_comment_id``
field on ``CommentCreate`` / ``CommentEntry`` and a side table
``subject_comment_replies`` keyed by ``comment_id``. Threads are
flat: a reply cannot itself have replies, so the wire shape never
needs a recursive renderer.

Tests cover:

- The happy path: posting a reply with ``parent_comment_id`` echoes
  the parent in the response, the parent's ``GET /replies``
  endpoint surfaces the new comment, and the per-subject
  ``GET /comments`` listing carries the parent id on the reply row.
- One-level enforcement: posting a reply *to* a reply 422s with the
  same diagnostic, regardless of the actor.
- Cross-subject smuggling: passing a parent_comment_id that lives on
  another dossier 404s — and 404, not 403, so the API never leaks
  whether the parent id is valid in some dossier the actor can see.
- Cascade: deleting a parent comment cascades to its replies via
  ``ON DELETE CASCADE`` on ``subject_comment_replies.parent_comment_id``;
  deleting the subject removes both parent and replies.
- Authorisation: any reader can post a reply (mirrors the comment
  create gate), the per-thread ``GET /replies`` 404s for outsiders,
  unauthenticated requests 401.
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


def _create_subject(client: TestClient, *, value: str = "alice") -> str:
    response = client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": value}},
    )
    assert response.status_code in (200, 201), response.text
    sid: str = response.json()["id"]
    return sid


def _post_comment(
    client: TestClient,
    sid: str,
    *,
    body: str = "Initial.",
    parent: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"body": body}
    if parent is not None:
        payload["parent_comment_id"] = parent
    response = client.post(f"/api/v1/subjects/{sid}/comments", json=payload)
    assert response.status_code == 201, response.text
    body_obj: dict[str, object] = response.json()
    return body_obj


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


def test_top_level_comment_has_no_parent(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    body = _post_comment(alice, sid, body="Top-level.")
    assert body["parent_comment_id"] is None


def test_reply_carries_parent_in_response_and_listing(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    parent = _post_comment(alice, sid, body="Parent.")
    parent_id = cast(int, parent["id"])

    reply = _post_comment(alice, sid, body="Reply.", parent=parent_id)
    assert reply["parent_comment_id"] == parent_id

    listing = alice.get(f"/api/v1/subjects/{sid}/comments").json()
    assert [c["parent_comment_id"] for c in listing] == [None, parent_id]


def test_replies_endpoint_lists_only_replies(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    parent = _post_comment(alice, sid, body="Parent.")
    parent_id = cast(int, parent["id"])
    other = _post_comment(alice, sid, body="Sibling top-level.")
    other_id = cast(int, other["id"])
    reply = _post_comment(alice, sid, body="Reply.", parent=parent_id)
    reply_id = cast(int, reply["id"])

    rows = alice.get(f"/api/v1/subjects/{sid}/comments/{parent_id}/replies").json()
    assert [r["id"] for r in rows] == [reply_id]
    assert all(r["parent_comment_id"] == parent_id for r in rows)
    assert other_id not in [r["id"] for r in rows]


def test_replies_endpoint_empty_for_top_level_with_no_replies(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    parent = _post_comment(alice, sid, body="Parent.")
    parent_id = cast(int, parent["id"])
    rows = alice.get(f"/api/v1/subjects/{sid}/comments/{parent_id}/replies").json()
    assert rows == []


def test_multiple_replies_returned_oldest_first(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    parent = _post_comment(alice, sid, body="Parent.")
    parent_id = cast(int, parent["id"])
    r1 = _post_comment(alice, sid, body="First reply.", parent=parent_id)
    r2 = _post_comment(alice, sid, body="Second reply.", parent=parent_id)
    rows = alice.get(f"/api/v1/subjects/{sid}/comments/{parent_id}/replies").json()
    assert [r["id"] for r in rows] == [cast(int, r1["id"]), cast(int, r2["id"])]


# --- validation ------------------------------------------------------------


def test_reply_to_unknown_parent_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.post(
        f"/api/v1/subjects/{sid}/comments",
        json={"body": "Reply to ghost.", "parent_comment_id": 999_999},
    )
    assert response.status_code == 404


def test_reply_to_parent_in_different_subject_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Cross-subject id smuggling 404s — the parent must live on the
    same dossier as the reply, no exceptions."""
    alice, _bob, _carol = trio_clients
    sid_a = _create_subject(alice, value="alice")
    sid_b = _create_subject(alice, value="bob")
    foreign_parent = _post_comment(alice, sid_a, body="On A.")
    foreign_id = cast(int, foreign_parent["id"])

    response = alice.post(
        f"/api/v1/subjects/{sid_b}/comments",
        json={"body": "Reply across subjects.", "parent_comment_id": foreign_id},
    )
    assert response.status_code == 404


def test_reply_to_reply_rejected_with_422(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """One-level threading: the parent must itself be a top-level
    comment. We emit 422 (rather than 400) so the FE can surface a
    field-level validation message tied to ``parent_comment_id``."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    parent = _post_comment(alice, sid, body="Parent.")
    parent_id = cast(int, parent["id"])
    reply = _post_comment(alice, sid, body="Reply.", parent=parent_id)
    reply_id = cast(int, reply["id"])

    response = alice.post(
        f"/api/v1/subjects/{sid}/comments",
        json={"body": "Reply to a reply.", "parent_comment_id": reply_id},
    )
    assert response.status_code == 422
    assert "one level deep" in response.json()["detail"]


def test_reply_with_blank_body_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Whitespace-only replies hit the same 422 the top-level path
    does — the body validation runs before threading checks so the
    error is consistent."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    parent = _post_comment(alice, sid, body="Parent.")
    parent_id = cast(int, parent["id"])

    response = alice.post(
        f"/api/v1/subjects/{sid}/comments",
        json={"body": "   ", "parent_comment_id": parent_id},
    )
    assert response.status_code == 422


# --- cascade ---------------------------------------------------------------


def test_deleting_parent_cascades_replies(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    parent = _post_comment(alice, sid, body="Parent.")
    parent_id = cast(int, parent["id"])
    _post_comment(alice, sid, body="Reply A.", parent=parent_id)
    _post_comment(alice, sid, body="Reply B.", parent=parent_id)

    assert alice.delete(f"/api/v1/subjects/{sid}/comments/{parent_id}").status_code == 204
    listing = alice.get(f"/api/v1/subjects/{sid}/comments").json()
    # Parent vanishes; both replies vanish with it.
    assert listing == []


def test_deleting_reply_does_not_affect_parent(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    parent = _post_comment(alice, sid, body="Parent.")
    parent_id = cast(int, parent["id"])
    reply = _post_comment(alice, sid, body="Reply.", parent=parent_id)
    reply_id = cast(int, reply["id"])

    assert alice.delete(f"/api/v1/subjects/{sid}/comments/{reply_id}").status_code == 204
    listing = alice.get(f"/api/v1/subjects/{sid}/comments").json()
    assert [c["id"] for c in listing] == [parent_id]


def test_deleting_subject_cascades_replies(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    parent = _post_comment(alice, sid, body="Parent.")
    parent_id = cast(int, parent["id"])
    _post_comment(alice, sid, body="Reply.", parent=parent_id)
    assert alice.delete(f"/api/v1/subjects/{sid}").status_code == 204
    # The subject is gone — every comments endpoint 404s.
    assert alice.get(f"/api/v1/subjects/{sid}/comments").status_code == 404


# --- authorisation ---------------------------------------------------------


def test_sharer_can_reply_and_list(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    parent = _post_comment(alice, sid, body="Parent.")
    parent_id = cast(int, parent["id"])
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    reply = _post_comment(bob, sid, body="Bob's reply.", parent=parent_id)
    assert reply["parent_comment_id"] == parent_id
    rows = bob.get(f"/api/v1/subjects/{sid}/comments/{parent_id}/replies").json()
    assert [r["body"] for r in rows] == ["Bob's reply."]


def test_outsider_cannot_post_or_list_replies(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, carol = trio_clients
    sid = _create_subject(alice)
    parent = _post_comment(alice, sid, body="Parent.")
    parent_id = cast(int, parent["id"])

    # Outsider cannot post a reply.
    response = carol.post(
        f"/api/v1/subjects/{sid}/comments",
        json={"body": "Sneaky.", "parent_comment_id": parent_id},
    )
    assert response.status_code == 404

    # Outsider cannot list replies either.
    assert carol.get(f"/api/v1/subjects/{sid}/comments/{parent_id}/replies").status_code == 404


def test_admin_can_reply_to_anything(
    client: TestClient,
    admin_token: str,
) -> None:
    _register(client, username="alice", password="alicepassword1")
    alice_token = _login(client, username="alice", password="alicepassword1")
    client.headers["Authorization"] = f"Bearer {alice_token}"
    sid = _create_subject(client)
    parent = _post_comment(client, sid, body="Parent.")
    parent_id = cast(int, parent["id"])

    client.headers["Authorization"] = f"Bearer {admin_token}"
    reply = _post_comment(client, sid, body="Admin reply.", parent=parent_id)
    assert reply["parent_comment_id"] == parent_id


def test_replies_endpoint_404_for_unknown_parent(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.get(f"/api/v1/subjects/{sid}/comments/99999/replies").status_code == 404


def test_unauthenticated_replies_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/subjects/sid/comments",
        json={"body": "Hi", "parent_comment_id": 1},
    )
    assert response.status_code == 401
    assert client.get("/api/v1/subjects/sid/comments/1/replies").status_code == 401
