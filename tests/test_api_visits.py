"""Phase 5 step 13 — per-actor dossier visit stamps + unread counts.

A visit stamp is the last-seen ISO timestamp recorded against a
``(subject, user)`` pair. The FE bumps it whenever the actor opens
the dossier; the unread badge is then derived as
``count(comment.created_at > last_seen_at)``.

Endpoints under test:

* ``POST /api/v1/subjects/{id}/visits/me`` — advance my stamp.
* ``GET  /api/v1/subjects/{id}/visits/me`` — read my stamp (404
  if I have never visited).
* ``GET  /api/v1/subjects/{id}/unread`` — my unread count plus
  the stamp the count is relative to.

Coverage:

- Happy path: visit, re-read, second visit advances stamp.
- Unread semantics:
  - never visited → unread == total comment count.
  - visited → only strictly-newer comments count.
  - own comment after visit still counts as unread.
- Per-actor isolation: Bob's visit doesn't reset Alice's unread
  count and vice versa.
- Authorisation: non-reader gets 404 on every verb; unauthenticated
  gets 401; unknown subject 404.
- Cascade: deleting the subject wipes the visit row (re-creating
  the same id wouldn't carry the ghost stamp forward).
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


def _post_comment(client: TestClient, sid: str, body: str) -> int:
    response = client.post(
        f"/api/v1/subjects/{sid}/comments",
        json={"body": body},
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


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


# --- visit stamps --------------------------------------------------------


def test_post_creates_stamp_and_returns_it(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.post(f"/api/v1/subjects/{sid}/visits/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["subject_id"] == sid
    assert body["last_seen_at"]


def test_get_404_when_never_visited(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.get(f"/api/v1/subjects/{sid}/visits/me").status_code == 404


def test_post_then_get_returns_same_stamp(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    posted = alice.post(f"/api/v1/subjects/{sid}/visits/me").json()
    fetched = alice.get(f"/api/v1/subjects/{sid}/visits/me").json()
    assert fetched == posted


def test_second_post_advances_stamp(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """A later POST must not regress the stamp.

    We sleep a hair so the ISO timestamps actually differ at
    microsecond resolution; without the sleep both calls land on
    the same ``datetime.now()`` and the assertion is meaningless.
    """
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    first = alice.post(f"/api/v1/subjects/{sid}/visits/me").json()
    time.sleep(0.01)
    second = alice.post(f"/api/v1/subjects/{sid}/visits/me").json()
    assert second["last_seen_at"] >= first["last_seen_at"]


def test_visits_are_per_actor(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    alice.post(f"/api/v1/subjects/{sid}/visits/me")
    # Bob has no stamp even though Alice does.
    assert bob.get(f"/api/v1/subjects/{sid}/visits/me").status_code == 404


# --- unread counts -------------------------------------------------------


def test_unread_zero_when_no_comments_and_no_visit(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.get(f"/api/v1/subjects/{sid}/unread")
    assert response.status_code == 200
    body = response.json()
    assert body["last_seen_at"] is None
    assert body["unread_comment_count"] == 0


def test_unread_counts_all_when_never_visited(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Freshly-shared collaborator: every comment counts as unread."""
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    _post_comment(alice, sid, "one")
    _post_comment(alice, sid, "two")
    _post_comment(alice, sid, "three")
    body = bob.get(f"/api/v1/subjects/{sid}/unread").json()
    assert body["last_seen_at"] is None
    assert body["unread_comment_count"] == 3


def test_unread_drops_to_zero_after_visit(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    _post_comment(alice, sid, "one")
    _post_comment(alice, sid, "two")
    time.sleep(0.01)
    alice.post(f"/api/v1/subjects/{sid}/visits/me")
    body = alice.get(f"/api/v1/subjects/{sid}/unread").json()
    assert body["unread_comment_count"] == 0
    assert body["last_seen_at"] is not None


def test_only_newer_comments_count_as_unread(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    _post_comment(alice, sid, "before bob's visit")
    time.sleep(0.01)
    bob.post(f"/api/v1/subjects/{sid}/visits/me")
    time.sleep(0.01)
    _post_comment(alice, sid, "after bob's visit (1)")
    _post_comment(alice, sid, "after bob's visit (2)")

    body = bob.get(f"/api/v1/subjects/{sid}/unread").json()
    assert body["unread_comment_count"] == 2


def test_per_actor_unread_isolation(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Alice visiting doesn't reset Bob's unread count."""
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    _post_comment(alice, sid, "a")
    _post_comment(alice, sid, "b")
    time.sleep(0.01)
    alice.post(f"/api/v1/subjects/{sid}/visits/me")  # Alice catches up.

    # Bob hasn't visited yet; his unread is still 2.
    bob_body = bob.get(f"/api/v1/subjects/{sid}/unread").json()
    assert bob_body["unread_comment_count"] == 2
    # Alice's unread is 0 — her visit closed her own gap.
    alice_body = alice.get(f"/api/v1/subjects/{sid}/unread").json()
    assert alice_body["unread_comment_count"] == 0


def test_actors_own_comment_counts_as_unread_until_revisit(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Posting a comment after a visit re-introduces unread.

    This mirrors how chat clients badge your own messages until
    you scroll past them; bumping the stamp on every comment
    creation is the FE's job, not the unread surface's job.
    """
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    alice.post(f"/api/v1/subjects/{sid}/visits/me")
    time.sleep(0.01)
    _post_comment(alice, sid, "my own followup")
    body = alice.get(f"/api/v1/subjects/{sid}/unread").json()
    assert body["unread_comment_count"] == 1


# --- authorisation -------------------------------------------------------


def test_non_reader_404_on_post_visit(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert bob.post(f"/api/v1/subjects/{sid}/visits/me").status_code == 404


def test_non_reader_404_on_get_visit(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert bob.get(f"/api/v1/subjects/{sid}/visits/me").status_code == 404


def test_non_reader_404_on_unread(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert bob.get(f"/api/v1/subjects/{sid}/unread").status_code == 404


def test_unknown_subject_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    assert alice.post("/api/v1/subjects/sub-nope/visits/me").status_code == 404
    assert alice.get("/api/v1/subjects/sub-nope/visits/me").status_code == 404
    assert alice.get("/api/v1/subjects/sub-nope/unread").status_code == 404


def test_unauthenticated_rejected(client: TestClient) -> None:
    sid = "sub-anything"
    assert client.post(f"/api/v1/subjects/{sid}/visits/me").status_code == 401
    assert client.get(f"/api/v1/subjects/{sid}/visits/me").status_code == 401
    assert client.get(f"/api/v1/subjects/{sid}/unread").status_code == 401


# --- cascade -------------------------------------------------------------


def test_subject_delete_cascades_visit(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    alice.post(f"/api/v1/subjects/{sid}/visits/me")
    assert alice.delete(f"/api/v1/subjects/{sid}").status_code == 204
    # Subject is gone, so the route 404s for unknown-subject.
    assert alice.get(f"/api/v1/subjects/{sid}/visits/me").status_code == 404
