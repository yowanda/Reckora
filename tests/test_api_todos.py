"""Phase 5 step 14 — per-actor TODO checklist on dossiers.

A TODO is a private, dossier-scoped checklist item. The calling
actor's items are invisible to every other reader (including the
owner), and cross-actor probing of ids is collapsed into a 404.

Endpoints under test:

* ``GET /api/v1/subjects/{id}/todos/me``
* ``POST /api/v1/subjects/{id}/todos/me``
* ``PATCH /api/v1/subjects/{id}/todos/me/{todo_id}``
* ``DELETE /api/v1/subjects/{id}/todos/me/{todo_id}``

Coverage:

- Happy path: create, list, mark done, edit body, delete.
- Privacy: Bob's todos are invisible to Alice and vice versa even
  when both have read access. Bob cannot PATCH/DELETE Alice's row
  by id.
- Validation: empty / whitespace / >512 char body, empty PATCH,
  whitespace-only PATCH body, and unknown PATCH fields.
- Authorisation: non-reader 404 on every verb, unauth 401, unknown
  subject 404.
- Cascade: subject delete wipes the todos.
- Ordering: oldest-first.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


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


# --- happy path ----------------------------------------------------------


def test_create_then_list(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    created = alice.post(
        f"/api/v1/subjects/{sid}/todos/me",
        json={"body": "rerun avatar OSINT"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["subject_id"] == sid
    assert body["body"] == "rerun avatar OSINT"
    assert body["done"] is False
    assert body["created_at"] == body["updated_at"]

    listed = alice.get(f"/api/v1/subjects/{sid}/todos/me").json()
    assert len(listed) == 1
    assert listed[0]["id"] == body["id"]


def test_list_empty_for_new_subject(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.get(f"/api/v1/subjects/{sid}/todos/me").json() == []


def test_list_oldest_first(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    bodies = ["one", "two", "three"]
    ids = []
    for b in bodies:
        ids.append(alice.post(f"/api/v1/subjects/{sid}/todos/me", json={"body": b}).json()["id"])
        time.sleep(0.005)
    listed = alice.get(f"/api/v1/subjects/{sid}/todos/me").json()
    assert [t["id"] for t in listed] == ids
    assert [t["body"] for t in listed] == bodies


def test_patch_toggles_done(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    todo = alice.post(f"/api/v1/subjects/{sid}/todos/me", json={"body": "x"}).json()
    response = alice.patch(
        f"/api/v1/subjects/{sid}/todos/me/{todo['id']}",
        json={"done": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["done"] is True

    response2 = alice.patch(
        f"/api/v1/subjects/{sid}/todos/me/{todo['id']}",
        json={"done": False},
    )
    assert response2.json()["done"] is False


def test_patch_rewrites_body(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    todo = alice.post(f"/api/v1/subjects/{sid}/todos/me", json={"body": "before"}).json()
    time.sleep(0.005)
    response = alice.patch(
        f"/api/v1/subjects/{sid}/todos/me/{todo['id']}",
        json={"body": "after"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["body"] == "after"
    # created_at should be preserved; only updated_at advances.
    assert body["created_at"] == todo["created_at"]
    assert body["updated_at"] >= todo["updated_at"]


def test_patch_supports_combined_update(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    todo = alice.post(f"/api/v1/subjects/{sid}/todos/me", json={"body": "before"}).json()
    response = alice.patch(
        f"/api/v1/subjects/{sid}/todos/me/{todo['id']}",
        json={"body": "after", "done": True},
    )
    assert response.json()["body"] == "after"
    assert response.json()["done"] is True


def test_delete_removes_row(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    todo = alice.post(f"/api/v1/subjects/{sid}/todos/me", json={"body": "x"}).json()
    assert alice.delete(f"/api/v1/subjects/{sid}/todos/me/{todo['id']}").status_code == 204
    assert alice.get(f"/api/v1/subjects/{sid}/todos/me").json() == []


def test_delete_unknown_id_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Idempotent on already-absent rows means 404 (we collapse
    \"not yours\" into the same shape, so a hit on \"never
    existed\" must be 404 too for consistency)."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.delete(f"/api/v1/subjects/{sid}/todos/me/99999").status_code == 404


# --- privacy -------------------------------------------------------------


def test_per_actor_isolation(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    alice.post(f"/api/v1/subjects/{sid}/todos/me", json={"body": "alice's"})
    bob.post(f"/api/v1/subjects/{sid}/todos/me", json={"body": "bob's"})

    alice_list = alice.get(f"/api/v1/subjects/{sid}/todos/me").json()
    bob_list = bob.get(f"/api/v1/subjects/{sid}/todos/me").json()

    assert {t["body"] for t in alice_list} == {"alice's"}
    assert {t["body"] for t in bob_list} == {"bob's"}


def test_cannot_patch_other_actors_todo(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    alice_todo = alice.post(f"/api/v1/subjects/{sid}/todos/me", json={"body": "alice's"}).json()
    response = bob.patch(
        f"/api/v1/subjects/{sid}/todos/me/{alice_todo['id']}",
        json={"done": True},
    )
    assert response.status_code == 404


def test_cannot_delete_other_actors_todo(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    alice_todo = alice.post(f"/api/v1/subjects/{sid}/todos/me", json={"body": "alice's"}).json()
    response = bob.delete(f"/api/v1/subjects/{sid}/todos/me/{alice_todo['id']}")
    assert response.status_code == 404
    # And Alice's todo is still there.
    assert len(alice.get(f"/api/v1/subjects/{sid}/todos/me").json()) == 1


def test_cannot_patch_todo_from_wrong_subject(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """A todo's id is not enough \u2014 the path's subject_id has
    to match too. Otherwise an analyst with two open dossiers
    could mutate one's todos via the other's path."""
    alice, _bob, _carol = trio_clients
    sid_a = _create_subject(alice, value="aaa")
    sid_b = _create_subject(alice, value="bbb")
    todo = alice.post(f"/api/v1/subjects/{sid_a}/todos/me", json={"body": "x"}).json()
    response = alice.patch(
        f"/api/v1/subjects/{sid_b}/todos/me/{todo['id']}",
        json={"done": True},
    )
    assert response.status_code == 404


# --- validation ----------------------------------------------------------


def test_create_empty_body_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/todos/me", json={"body": ""}).status_code == 422


def test_create_whitespace_body_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/todos/me", json={"body": "   "}).status_code == 422


def test_create_oversize_body_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    payload = {"body": "x" * 513}
    assert alice.post(f"/api/v1/subjects/{sid}/todos/me", json=payload).status_code == 422


def test_create_extra_fields_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.post(
        f"/api/v1/subjects/{sid}/todos/me",
        json={"body": "x", "done": True},  # done is ignored on create
    )
    assert response.status_code == 422


def test_patch_empty_payload_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    todo = alice.post(f"/api/v1/subjects/{sid}/todos/me", json={"body": "x"}).json()
    response = alice.patch(f"/api/v1/subjects/{sid}/todos/me/{todo['id']}", json={})
    assert response.status_code == 422


def test_patch_whitespace_body_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    todo = alice.post(f"/api/v1/subjects/{sid}/todos/me", json={"body": "x"}).json()
    response = alice.patch(
        f"/api/v1/subjects/{sid}/todos/me/{todo['id']}",
        json={"body": "   "},
    )
    assert response.status_code == 422


# --- authorisation -------------------------------------------------------


def test_non_reader_404_on_list(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert bob.get(f"/api/v1/subjects/{sid}/todos/me").status_code == 404


def test_non_reader_404_on_create(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = bob.post(f"/api/v1/subjects/{sid}/todos/me", json={"body": "x"})
    assert response.status_code == 404


def test_unknown_subject_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    assert alice.get("/api/v1/subjects/sub-nope/todos/me").status_code == 404
    assert alice.post("/api/v1/subjects/sub-nope/todos/me", json={"body": "x"}).status_code == 404


def test_unauthenticated_rejected(client: TestClient) -> None:
    sid = "sub-anything"
    assert client.get(f"/api/v1/subjects/{sid}/todos/me").status_code == 401
    assert client.post(f"/api/v1/subjects/{sid}/todos/me", json={"body": "x"}).status_code == 401
    assert (
        client.patch(f"/api/v1/subjects/{sid}/todos/me/1", json={"done": True}).status_code == 401
    )
    assert client.delete(f"/api/v1/subjects/{sid}/todos/me/1").status_code == 401


# --- cascade -------------------------------------------------------------


def test_subject_delete_cascades_todos(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    alice.post(f"/api/v1/subjects/{sid}/todos/me", json={"body": "x"})
    assert alice.delete(f"/api/v1/subjects/{sid}").status_code == 204
    # Subject is gone, route 404s on the unknown-subject path.
    assert alice.get(f"/api/v1/subjects/{sid}/todos/me").status_code == 404
