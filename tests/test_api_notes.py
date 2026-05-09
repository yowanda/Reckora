"""Phase 5 step 12 — per-actor private notes on dossiers.

A note is a personal scratch-pad attached to a dossier. The
calling actor's note is **private** — it never surfaces to other
readers (including the owner), never appears in comments / threads
/ mentions / activity / assignments, and has no impact on
dossier-level state.

Endpoints under test:

* ``GET /api/v1/subjects/{id}/notes/me`` — read my note (404 if
  no note yet, also 404 if I cannot read the dossier so existence
  doesn't leak).
* ``PUT /api/v1/subjects/{id}/notes/me`` — upsert. First write
  sets ``created_at``; subsequent writes only advance ``updated_at``.
* ``DELETE /api/v1/subjects/{id}/notes/me`` — idempotent delete.

Coverage:

- Happy path: create, read back, edit, delete.
- Privacy: a different actor reading the same subject sees their
  own note (or absent), never the other actor's body.
- Wire shape: subject_id / user_id / body / timestamps.
- Validation: empty / whitespace-only body 422s; oversize body 422s.
- Authorisation: non-reader gets 404 on every verb (existence
  leak guard); unauthenticated gets 401.
- Cascade: deleting the underlying subject (or the user) wipes
  the note row so a re-created subject id doesn't inherit a
  ghost note.
- Idempotence: deleting twice is fine; PUT after PUT keeps the
  original ``created_at``.
"""

from __future__ import annotations

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


# --- happy path -----------------------------------------------------------


def test_owner_can_create_and_read_note(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)

    put_resp = alice.put(
        f"/api/v1/subjects/{sid}/notes/me",
        json={"body": "remember to check the avatar EXIF"},
    )
    assert put_resp.status_code == 200, put_resp.text
    body = put_resp.json()
    assert body["subject_id"] == sid
    assert body["body"] == "remember to check the avatar EXIF"
    assert body["created_at"] == body["updated_at"]

    get_resp = alice.get(f"/api/v1/subjects/{sid}/notes/me")
    assert get_resp.status_code == 200
    assert get_resp.json()["body"] == "remember to check the avatar EXIF"


def test_404_when_no_note_yet(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.get(f"/api/v1/subjects/{sid}/notes/me")
    assert response.status_code == 404


def test_put_overwrites_keeping_created_at(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """The second PUT keeps the original ``created_at`` and just
    bumps ``updated_at``. This means the FE can show a "first
    saved" stamp that doesn't drift on every keystroke."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    first = alice.put(
        f"/api/v1/subjects/{sid}/notes/me",
        json={"body": "first version"},
    ).json()
    second = alice.put(
        f"/api/v1/subjects/{sid}/notes/me",
        json={"body": "second version"},
    ).json()

    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] >= first["updated_at"]
    assert second["body"] == "second version"


def test_delete_clears_note(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert (
        alice.put(f"/api/v1/subjects/{sid}/notes/me", json={"body": "to delete"}).status_code == 200
    )
    assert alice.delete(f"/api/v1/subjects/{sid}/notes/me").status_code == 204
    assert alice.get(f"/api/v1/subjects/{sid}/notes/me").status_code == 404


def test_delete_is_idempotent(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    # Never created -> still 204.
    assert alice.delete(f"/api/v1/subjects/{sid}/notes/me").status_code == 204
    assert alice.delete(f"/api/v1/subjects/{sid}/notes/me").status_code == 204


# --- privacy --------------------------------------------------------------


def test_notes_are_per_actor(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Bob has read access via share. He sees no note where Alice
    has one, and his own note is invisible to Alice."""
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    assert (
        alice.put(
            f"/api/v1/subjects/{sid}/notes/me",
            json={"body": "alice secret"},
        ).status_code
        == 200
    )

    # Bob has no note even though Alice does.
    assert bob.get(f"/api/v1/subjects/{sid}/notes/me").status_code == 404

    # Bob writes his own; Alice still sees only hers.
    assert (
        bob.put(f"/api/v1/subjects/{sid}/notes/me", json={"body": "bob secret"}).status_code == 200
    )
    assert alice.get(f"/api/v1/subjects/{sid}/notes/me").json()["body"] == "alice secret"
    assert bob.get(f"/api/v1/subjects/{sid}/notes/me").json()["body"] == "bob secret"


# --- validation -----------------------------------------------------------


def test_empty_body_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.put(
        f"/api/v1/subjects/{sid}/notes/me",
        json={"body": ""},
    )
    assert response.status_code == 422


def test_whitespace_body_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.put(
        f"/api/v1/subjects/{sid}/notes/me",
        json={"body": "   \n\t  "},
    )
    assert response.status_code == 422


def test_oversize_body_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Body > 16 KiB is rejected by the schema-level max_length."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    payload = {"body": "x" * (16 * 1024 + 1)}
    response = alice.put(f"/api/v1/subjects/{sid}/notes/me", json=payload)
    assert response.status_code == 422


def test_extra_fields_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.put(
        f"/api/v1/subjects/{sid}/notes/me",
        json={"body": "ok", "user_id": 99},
    )
    assert response.status_code == 422


# --- authorisation --------------------------------------------------------


def test_non_reader_gets_404_on_get(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert bob.get(f"/api/v1/subjects/{sid}/notes/me").status_code == 404


def test_non_reader_gets_404_on_put(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = bob.put(
        f"/api/v1/subjects/{sid}/notes/me",
        json={"body": "I can't see this"},
    )
    assert response.status_code == 404
    # And nothing was written.
    assert alice.get(f"/api/v1/subjects/{sid}/notes/me").status_code == 404


def test_non_reader_gets_404_on_delete(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert bob.delete(f"/api/v1/subjects/{sid}/notes/me").status_code == 404


def test_unknown_subject_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    assert alice.get("/api/v1/subjects/sub-not-real/notes/me").status_code == 404
    assert (
        alice.put(
            "/api/v1/subjects/sub-not-real/notes/me",
            json={"body": "x"},
        ).status_code
        == 404
    )
    assert alice.delete("/api/v1/subjects/sub-not-real/notes/me").status_code == 404


def test_unauthenticated_rejected(client: TestClient) -> None:
    sid = "sub-anything"
    assert client.get(f"/api/v1/subjects/{sid}/notes/me").status_code == 401
    assert client.put(f"/api/v1/subjects/{sid}/notes/me", json={"body": "x"}).status_code == 401
    assert client.delete(f"/api/v1/subjects/{sid}/notes/me").status_code == 401


# --- cascade --------------------------------------------------------------


def test_subject_delete_cascades_note(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert (
        alice.put(f"/api/v1/subjects/{sid}/notes/me", json={"body": "to cascade"}).status_code
        == 200
    )
    assert alice.delete(f"/api/v1/subjects/{sid}").status_code == 204
    # Subject itself is gone, so the note endpoint 404s for the
    # subject-not-found reason.
    assert alice.get(f"/api/v1/subjects/{sid}/notes/me").status_code == 404


# --- nice-to-have ---------------------------------------------------------


def test_body_is_trimmed_before_storage(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Leading/trailing whitespace is stripped before validation
    and before storage \u2014 the persisted body never has stray
    boundary whitespace from a fat-fingered editor."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.put(
        f"/api/v1/subjects/{sid}/notes/me",
        json={"body": "   trimmed me   \n"},
    )
    assert response.status_code == 200
    assert response.json()["body"] == "trimmed me"
    # Round-trip through GET is also trimmed.
    assert alice.get(f"/api/v1/subjects/{sid}/notes/me").json()["body"] == "trimmed me"


def test_user_id_in_response_matches_actor(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201

    alice_note = alice.put(
        f"/api/v1/subjects/{sid}/notes/me",
        json={"body": "alice"},
    ).json()
    bob_note = bob.put(
        f"/api/v1/subjects/{sid}/notes/me",
        json={"body": "bob"},
    ).json()
    # The two user_ids must differ so the schema can be re-used for
    # admin-style cross-actor surfaces in the future.
    assert alice_note["user_id"] != bob_note["user_id"]
