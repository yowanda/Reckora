"""Phase 5 step 3 — per-dossier activity feed.

The feed is a read-only chronological projection over the four tables
that already record observable mutations on a dossier (comments,
assignees, shares, the cross-trace anchor). Tests cover:

- Each event kind surfaces with the right ``actor`` / ``target`` /
  ``excerpt`` shape.
- Ordering is newest-first with a stable tiebreaker.
- Access control mirrors the comments endpoint: owner / sharer /
  assignee / admin can read; outsiders get 404 (no existence leak).
- ``limit`` is enforced and validated.
- A subject with no activity returns an empty list (not 404).
- Comment excerpt is truncated server-side to 200 characters.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Sequence
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from reckora.models.entity import Trace
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


def _stub_anchor_traces() -> Callable[[Sequence[Trace]], Awaitable[object]]:
    """Mirror of the helper in ``test_api_investigations`` so we can mint a
    deterministic anchor without re-importing across test modules."""
    from reckora.evidence.anchor import Anchor
    from reckora.evidence.merkle import compute_dossier_root
    from reckora.evidence.timestamp import CalendarReceipt

    async def _fake(traces: Sequence[Trace]) -> Anchor:
        root, leaves = compute_dossier_root(traces)
        return Anchor(
            merkle_root=root,
            leaf_hashes=leaves,
            receipts=[
                CalendarReceipt(
                    calendar_url="https://stub.calendar.example",
                    receipt_b64="ZmFrZQ==",
                    submitted_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            ],
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    return _fake


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
    """Owner (alice), sharer/assignee (bob), outsider (carol)."""
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


def test_empty_dossier_yields_empty_feed(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """A freshly-saved dossier has no comments / assignees / shares; the
    feed must return ``[]`` rather than 404 — readers should be able to
    open the activity panel before anything has happened."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.get(f"/api/v1/subjects/{sid}/activity")
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_comment_event_carries_author_and_excerpt(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    posted = alice.post(
        f"/api/v1/subjects/{sid}/comments",
        json={"body": "Initial intake notes."},
    )
    assert posted.status_code == 201

    rows = alice.get(f"/api/v1/subjects/{sid}/activity").json()
    assert len(rows) == 1
    event = rows[0]
    assert event["kind"] == "comment_added"
    assert event["actor_username"] == "alice"
    assert event["target_user_id"] is None
    assert event["target_username"] is None
    assert event["excerpt"] == "Initial intake notes."


def test_assigned_event_carries_actor_and_target(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "bob"})
    assert response.status_code == 201

    rows = alice.get(f"/api/v1/subjects/{sid}/activity").json()
    kinds = [r["kind"] for r in rows]
    assert "assigned" in kinds
    assigned = next(r for r in rows if r["kind"] == "assigned")
    assert assigned["actor_username"] == "alice"
    assert assigned["target_username"] == "bob"
    assert assigned["excerpt"] is None


def test_shared_event_has_target_only(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """``subject_shares`` does not record a granter, so the share event
    surfaces with ``actor_user_id`` left null on purpose."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"})
    assert response.status_code == 201

    rows = alice.get(f"/api/v1/subjects/{sid}/activity").json()
    shared = next(r for r in rows if r["kind"] == "shared")
    assert shared["actor_user_id"] is None
    assert shared["actor_username"] is None
    assert shared["target_username"] == "bob"


def test_anchored_event_present_when_dossier_anchored(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """A dossier saved with ``anchor: true`` must surface an ``anchored``
    event. Actor / target are both null — the engine mints the anchor,
    not a specific user."""
    alice, _bob, _carol = trio_clients
    with patch(
        "reckora_api.investigations.routes.anchor_traces",
        side_effect=_stub_anchor_traces(),
    ):
        response = alice.post(
            "/api/v1/investigations",
            json={"seed": {"kind": "username", "value": "alice"}, "anchor": True},
        )
    assert response.status_code == 201, response.text
    sid = response.json()["id"]

    rows = alice.get(f"/api/v1/subjects/{sid}/activity").json()
    anchored = next(r for r in rows if r["kind"] == "anchored")
    assert anchored["actor_user_id"] is None
    assert anchored["target_user_id"] is None
    assert anchored["excerpt"] is None
    # The anchored event reuses the subject's own ``created_at`` so the
    # frontend can render "anchored on save" without a heuristic.
    assert anchored["created_at"]


def test_dossier_without_anchor_omits_anchored_event(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)  # default: no anchor
    rows = alice.get(f"/api/v1/subjects/{sid}/activity").json()
    assert all(r["kind"] != "anchored" for r in rows)


def test_feed_combines_all_event_kinds_newest_first(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """A dossier with all four event kinds returns them newest-first.

    We assert two invariants that hold regardless of sub-second
    timestamp resolution:

    1. All four kinds show up exactly once.
    2. ``anchored`` is the oldest (it pins to the subject's own
       ``created_at``, which is strictly earlier than every other
       row by definition).

    The relative order of ``comment_added`` / ``assigned`` /
    ``shared`` is intentionally unconstrained — they can land in
    the same SQLite second, in which case the deterministic
    tiebreaker (per :meth:`AccessRepository.list_activity`) takes
    over but does not match strict insertion order.
    """
    alice, _bob, _carol = trio_clients
    with patch(
        "reckora_api.investigations.routes.anchor_traces",
        side_effect=_stub_anchor_traces(),
    ):
        sid = alice.post(
            "/api/v1/investigations",
            json={"seed": {"kind": "username", "value": "alice"}, "anchor": True},
        ).json()["id"]
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    assert (
        alice.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "bob"}).status_code == 201
    )
    assert (
        alice.post(f"/api/v1/subjects/{sid}/comments", json={"body": "Triaged."}).status_code == 201
    )

    rows = alice.get(f"/api/v1/subjects/{sid}/activity").json()
    kinds = [r["kind"] for r in rows]
    assert sorted(kinds) == ["anchored", "assigned", "comment_added", "shared"]
    assert kinds[-1] == "anchored"


def test_long_comment_excerpt_is_truncated(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """The feed projects a leading slice of the comment body — a 10k-char
    comment must not produce a 10k-char excerpt."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    body = "x" * 5000
    assert alice.post(f"/api/v1/subjects/{sid}/comments", json={"body": body}).status_code == 201

    rows = alice.get(f"/api/v1/subjects/{sid}/activity").json()
    excerpt = rows[0]["excerpt"]
    assert excerpt is not None
    assert len(excerpt) <= 200
    assert excerpt == "x" * 200


# --- access control --------------------------------------------------------


def test_sharer_can_read_activity(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    assert alice.post(f"/api/v1/subjects/{sid}/comments", json={"body": "n"}).status_code == 201

    response = bob.get(f"/api/v1/subjects/{sid}/activity")
    assert response.status_code == 200
    kinds = {r["kind"] for r in response.json()}
    assert {"comment_added", "shared"} <= kinds


def test_assignee_can_read_activity(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Assignment grants implicit read access — the activity feed must
    honour that without forcing an explicit share."""
    alice, bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert (
        alice.post(f"/api/v1/subjects/{sid}/assignees", json={"username": "bob"}).status_code == 201
    )

    response = bob.get(f"/api/v1/subjects/{sid}/activity")
    assert response.status_code == 200
    assert any(r["kind"] == "assigned" for r in response.json())


def test_outsider_cannot_read_activity(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Carol has no access — she gets 404 (not 403) so we do not leak
    the existence of the dossier to non-readers."""
    alice, _bob, carol = trio_clients
    sid = _create_subject(alice)
    assert (
        alice.post(f"/api/v1/subjects/{sid}/comments", json={"body": "private"}).status_code == 201
    )
    assert carol.get(f"/api/v1/subjects/{sid}/activity").status_code == 404


def test_admin_can_read_activity_on_any_subject(
    client: TestClient,
    admin_token: str,
) -> None:
    """Admins skip the access check the same way they do for comments
    and assignees — they need to triage legacy un-owned dossiers."""
    _register(client, username="alice", password="alicepassword1")
    alice_token = _login(client, username="alice", password="alicepassword1")
    _set_auth(client, alice_token)
    sid = _create_subject(client)
    assert client.post(f"/api/v1/subjects/{sid}/comments", json={"body": "x"}).status_code == 201

    _set_auth(client, admin_token)
    response = client.get(f"/api/v1/subjects/{sid}/activity")
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_unknown_subject_404(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    assert alice.get("/api/v1/subjects/subj-missing/activity").status_code == 404


def test_activity_endpoint_requires_auth(client: TestClient) -> None:
    """Mirror of the auth-guard tests on the comments endpoint — no
    bearer token, no feed."""
    response = client.get("/api/v1/subjects/subj-anything/activity")
    assert response.status_code == 401


# --- limit -----------------------------------------------------------------


def test_limit_caps_returned_rows(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    for n in range(5):
        assert (
            alice.post(f"/api/v1/subjects/{sid}/comments", json={"body": str(n)}).status_code == 201
        )

    full = alice.get(f"/api/v1/subjects/{sid}/activity").json()
    assert len(full) == 5
    capped = alice.get(f"/api/v1/subjects/{sid}/activity?limit=2").json()
    assert len(capped) == 2
    # Newest-first: the two most recent comments.
    assert [r["excerpt"] for r in capped] == ["4", "3"]


@pytest.mark.parametrize("bad_limit", [0, -1, 201])
def test_limit_validation(
    trio_clients: tuple[TestClient, TestClient, TestClient],
    bad_limit: int,
) -> None:
    """``limit`` is constrained to ``[1, 200]`` so a hostile client
    can't ask the API for an unbounded scan."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    response = alice.get(f"/api/v1/subjects/{sid}/activity?limit={bad_limit}")
    assert response.status_code == 422


# --- cascade ---------------------------------------------------------------


def test_deleting_subject_clears_activity(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Once the subject is gone the feed must 404 — same surface as the
    comments endpoint, so the frontend doesn't have to special-case
    "subject was deleted but feed still readable"."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/comments", json={"body": "x"}).status_code == 201
    assert alice.delete(f"/api/v1/subjects/{sid}").status_code == 204
    assert alice.get(f"/api/v1/subjects/{sid}/activity").status_code == 404
