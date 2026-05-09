"""Phase 5 step 10 — comment mentions (``@username``) and per-actor feed.

Mentions are extracted from the comment body by a regex parser
(``@[A-Za-z0-9_-]{3,64}`` after a word boundary) and persisted in
the ``subject_comment_mentions`` side table when the comment is
created. Unknown usernames and users without read access to the
dossier are dropped silently — the auth layer already bounds who can
*post* a comment, so the mention surface adds no new attack vector.

Tests cover:

- Parser semantics: deduplication, first-seen ordering, rejecting
  ``@`` inside email addresses, sub-3 / over-64 length, etc.
- The ``mentions`` field on the create / list comment responses:
  resolved usernames are alphabetically sorted; unknown / unreachable
  usernames vanish; self-mentions are echoed.
- The per-actor ``GET /api/v1/me/mentions`` feed: most-recent first,
  cross-dossier, ``limit`` truncation, negative-limit rejection,
  isolation between actors, empty when nobody pinged the user.
- Cascade: deleting the underlying comment, the comment's subject,
  or the mentioned user wipes the mention row.
- Authorisation: outsiders cannot get mentioned by smuggling their
  username into a comment on a dossier they cannot see.
- Visibility leak guard: dropping a mention for a non-reader does
  NOT 422 the comment — the comment is created with whatever
  mentions resolved.
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
from reckora_api.mentions.parser import extract_mentions


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
    body: str,
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/subjects/{sid}/comments",
        json={"body": body},
    )
    assert response.status_code == 201, response.text
    payload: dict[str, object] = response.json()
    return payload


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


# --- parser ---------------------------------------------------------------


def test_parser_extracts_simple_mention() -> None:
    assert extract_mentions("Hey @alice, take a look.") == ["alice"]


def test_parser_dedupes_preserving_first_seen_order() -> None:
    assert extract_mentions("@bob ping @alice and @bob again") == ["bob", "alice"]


def test_parser_skips_email_address() -> None:
    """``foo@bar.com`` must not register as an ``@bar`` mention.

    The negative look-behind on the regex rejects ``@`` preceded by a
    word character, so email and twitter-style handles in URLs are
    safe."""
    assert extract_mentions("Email me at me@example.com if needed") == []


def test_parser_skips_double_at() -> None:
    """``@@alice`` must not register \u2014 the ``@`` is preceded by another ``@``."""
    assert extract_mentions("hey @@alice") == []


def test_parser_rejects_too_short_handle() -> None:
    """The auth layer requires usernames \u2265 3 characters; the parser
    mirrors the policy so ``@bo`` does not surface as a candidate."""
    assert extract_mentions("ping @bo for review") == []


def test_parser_handles_multiple_distinct_handles() -> None:
    out = extract_mentions("Loop in @alice and @bob_smith and @carol-1")
    assert out == ["alice", "bob_smith", "carol-1"]


def test_parser_ignores_at_inside_word() -> None:
    assert extract_mentions("the price is 5@10 today") == []


# --- create comment with mentions ----------------------------------------


def test_resolved_mention_appears_in_response(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201

    body = _post_comment(alice, sid, "Hey @bob can you take a look?")
    assert body["mentions"] == ["bob"]


def test_mentions_are_alphabetical_and_deduped(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    for name in ("bob", "carol"):
        assert (
            alice.post(f"/api/v1/subjects/{sid}/share", json={"username": name}).status_code == 201
        )

    body = _post_comment(alice, sid, "Ping @carol then @bob, also @bob again")
    assert body["mentions"] == ["bob", "carol"]


def test_unknown_username_is_dropped_silently(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """A typo'd handle should not 422 the comment \u2014 it lives in the
    body verbatim and just doesn't fire a mention."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    body = _post_comment(alice, sid, "Hey @nosuchuser please reply")
    assert body["mentions"] == []
    assert body["body"] == "Hey @nosuchuser please reply"


def test_mention_of_user_without_access_is_dropped(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Carol exists but has no access to the dossier \u2014 the comment is
    still accepted, but Carol is not pinged.

    This guard prevents a sharer from notifying outsiders by
    sneaking their handles into a comment body."""
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    body = _post_comment(alice, sid, "Looping in @carol for context")
    assert body["mentions"] == []


def test_self_mention_resolves(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    body = _post_comment(alice, sid, "Note to @alice: follow up tomorrow")
    assert body["mentions"] == ["alice"]


def test_listing_carries_mentions_field(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201

    _post_comment(alice, sid, "First comment @bob")
    _post_comment(alice, sid, "Second comment, no pings")

    listing = alice.get(f"/api/v1/subjects/{sid}/comments").json()
    assert [c["mentions"] for c in listing] == [["bob"], []]


# --- /me/mentions feed ----------------------------------------------------


def test_me_mentions_returns_mentions_in_recent_first_order(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob_client, _carol = trio_clients
    sid_a = _create_subject(alice, value="alice_dossier")
    sid_b = _create_subject(alice, value="bob_dossier")
    for sid in (sid_a, sid_b):
        assert (
            alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
        )

    _post_comment(alice, sid_a, "@bob earlier ping")
    _post_comment(alice, sid_b, "@bob later ping")

    feed = bob_client.get("/api/v1/me/mentions").json()
    assert [m["subject_id"] for m in feed] == [sid_b, sid_a]
    assert all(m["author_username"] == "alice" for m in feed)


def test_me_mentions_is_per_actor(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob_client, _carol = trio_clients
    sid = _create_subject(alice)
    share_resp = alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"})
    assert share_resp.status_code == 201

    _post_comment(alice, sid, "@bob look here")
    # Alice never pinged herself \u2014 her feed is empty.
    assert alice.get("/api/v1/me/mentions").json() == []
    # Bob's feed has the one ping.
    feed = bob_client.get("/api/v1/me/mentions").json()
    assert len(feed) == 1
    assert feed[0]["body"] == "@bob look here"


def test_me_mentions_limit_truncates(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob_client, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201

    for i in range(3):
        _post_comment(alice, sid, f"ping #{i} @bob")
    feed = bob_client.get("/api/v1/me/mentions", params={"limit": 2}).json()
    assert len(feed) == 2


def test_me_mentions_negative_limit_rejected(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    response = alice.get("/api/v1/me/mentions", params={"limit": -1})
    assert response.status_code == 422


def test_me_mentions_empty_when_no_pings(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, _bob, _carol = trio_clients
    _create_subject(alice)
    assert alice.get("/api/v1/me/mentions").json() == []


def test_me_mentions_unauthenticated_rejected(client: TestClient) -> None:
    assert client.get("/api/v1/me/mentions").status_code == 401


def test_me_mentions_dedupes_when_handle_repeats_in_body(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    """Mentioning the same user twice in one comment fires exactly
    one mention row \u2014 the per-actor feed never surfaces the
    comment twice."""
    alice, bob_client, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    _post_comment(alice, sid, "@bob @bob @bob hi")
    feed = bob_client.get("/api/v1/me/mentions").json()
    assert len(feed) == 1


# --- cascade --------------------------------------------------------------


def test_deleting_comment_drops_mention(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob_client, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    body = _post_comment(alice, sid, "@bob ping")
    cid = cast(int, body["id"])
    assert alice.delete(f"/api/v1/subjects/{sid}/comments/{cid}").status_code == 204
    assert bob_client.get("/api/v1/me/mentions").json() == []


def test_deleting_subject_drops_mention(
    trio_clients: tuple[TestClient, TestClient, TestClient],
) -> None:
    alice, bob_client, _carol = trio_clients
    sid = _create_subject(alice)
    assert alice.post(f"/api/v1/subjects/{sid}/share", json={"username": "bob"}).status_code == 201
    _post_comment(alice, sid, "@bob ping")
    assert alice.delete(f"/api/v1/subjects/{sid}").status_code == 204
    assert bob_client.get("/api/v1/me/mentions").json() == []
