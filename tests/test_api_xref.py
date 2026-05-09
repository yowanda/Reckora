"""Phase 5 — cross-reference endpoint (shared evidence library).

End-to-end coverage for ``GET /api/v1/subjects/{id}/cross-references``:

- The endpoint groups by identifier and within each group orders matched
  dossiers newest-first, with ``owner_username`` populated from
  :class:`reckora_api.auth.repository.UserRepository`.
- Visibility honours owner/share/admin semantics from
  :class:`reckora_api.access.repository.AccessRepository.list_cross_references`,
  matching the rest of the read API: viewers only see matches they own
  or have been explicitly shared on.
- 404 (not 403) is returned when the *source* subject isn't visible to
  the actor — same posture as ``GET /api/v1/subjects/{id}`` so we don't
  leak whether a subject id exists.
- Identifiers with zero visible cross-references are omitted from the
  response (no empty groups), and the same matched subject never shows
  up twice under the same identifier (``subject_identifiers`` is the
  unique index that guarantees this).
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


def _create(client: TestClient, kind: str, value: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": kind, "value": value}},
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def _xref(client: TestClient, subject_id: str) -> tuple[int, dict[str, object]]:
    response = client.get(f"/api/v1/subjects/{subject_id}/cross-references")
    return response.status_code, response.json()


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
def two_viewer_clients(client: TestClient) -> Iterator[tuple[TestClient, TestClient]]:
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


@pytest.fixture
def three_viewer_clients(
    client: TestClient,
) -> Iterator[tuple[TestClient, TestClient, TestClient]]:
    _register(client, username="alice", password="alicepassword1")
    _register(client, username="bob", password="bobpassword12")
    _register(client, username="carol", password="carolpassword123")

    a_token = _login(client, username="alice", password="alicepassword1")
    b_token = _login(client, username="bob", password="bobpassword12")
    c_token = _login(client, username="carol", password="carolpassword123")

    alice = TestClient(client.app)
    alice.headers["Authorization"] = f"Bearer {a_token}"
    bob = TestClient(client.app)
    bob.headers["Authorization"] = f"Bearer {b_token}"
    carol = TestClient(client.app)
    carol.headers["Authorization"] = f"Bearer {c_token}"
    try:
        yield alice, bob, carol
    finally:
        alice.close()
        bob.close()
        carol.close()


# --- baseline: empty / single-dossier --------------------------------------


def test_xref_empty_when_no_other_dossier_shares_identifier(
    authed_client: TestClient,
) -> None:
    """A solo investigation has no cross-references — empty ``items`` list."""
    s = _create(authed_client, "username", "alice")
    code, body = _xref(authed_client, str(s["id"]))
    assert code == 200, body
    assert body == {"items": []}


def test_xref_returns_404_for_unknown_subject(authed_client: TestClient) -> None:
    code, body = _xref(authed_client, "subj-does-not-exist")
    assert code == 404
    assert "no saved dossier" in str(body["detail"]).lower()


def test_xref_does_not_include_source_subject_in_matches(
    authed_client: TestClient,
) -> None:
    """The source dossier is excluded from its own cross-reference list."""
    s = _create(authed_client, "username", "alice")
    code, body = _xref(authed_client, str(s["id"]))
    assert code == 200
    items_value = body["items"]
    assert isinstance(items_value, list)
    assert all(match["id"] != s["id"] for entry in items_value for match in entry["subjects"])


# --- two-dossier owner cross-reference -------------------------------------


def test_owner_sees_their_own_dossiers_in_cross_reference(
    authed_client: TestClient,
) -> None:
    """Two dossiers owned by the same user that share an identifier are linked."""
    a = _create(authed_client, "username", "alice")
    b = _create(authed_client, "username", "alice")
    assert a["id"] != b["id"]  # distinct subjects despite the same seed

    code, body = _xref(authed_client, str(a["id"]))
    assert code == 200
    items_value = body["items"]
    assert isinstance(items_value, list)

    # The fake collector trace is itself an identifier on the dossier
    # alongside the seed, so both dossiers may share *multiple*
    # identifiers — we just need to confirm dossier ``b`` is reachable.
    matches = {match["id"] for entry in items_value for match in entry["subjects"]}
    assert b["id"] in matches


# --- viewer/owner access filtering -----------------------------------------


def test_viewer_does_not_see_other_owners_dossiers_in_cross_reference(
    two_viewer_clients: tuple[TestClient, TestClient],
) -> None:
    """Bob's dossier sharing an identifier with Alice's must NOT be visible
    to Alice in the cross-reference response unless Bob shared with her.
    """
    alice, bob = two_viewer_clients
    alice_subject = _create(alice, "username", "alice")
    _create(bob, "username", "alice")  # shares the seed identifier

    code, body = _xref(alice, str(alice_subject["id"]))
    assert code == 200
    assert body == {"items": []}


def test_shared_dossier_appears_in_cross_reference(
    two_viewer_clients: tuple[TestClient, TestClient],
) -> None:
    """After sharing, the previously-invisible dossier is surfaced."""
    alice, bob = two_viewer_clients
    alice_subject = _create(alice, "username", "alice")
    bob_subject = _create(bob, "username", "alice")

    response = bob.post(
        f"/api/v1/subjects/{bob_subject['id']}/share",
        json={"username": "alice"},
    )
    assert response.status_code == 201, response.text

    code, body = _xref(alice, str(alice_subject["id"]))
    assert code == 200
    items_value = body["items"]
    assert isinstance(items_value, list)
    matches = {match["id"] for entry in items_value for match in entry["subjects"]}
    assert bob_subject["id"] in matches
    # owner_username is populated for matches
    for entry in items_value:
        for match in entry["subjects"]:
            if match["id"] == bob_subject["id"]:
                assert match["owner_username"] == "bob"


def test_revoking_share_drops_dossier_from_cross_reference(
    two_viewer_clients: tuple[TestClient, TestClient],
) -> None:
    alice, bob = two_viewer_clients
    alice_subject = _create(alice, "username", "alice")
    bob_subject = _create(bob, "username", "alice")
    bob.post(
        f"/api/v1/subjects/{bob_subject['id']}/share",
        json={"username": "alice"},
    )
    bob.delete(f"/api/v1/subjects/{bob_subject['id']}/share/alice")

    code, body = _xref(alice, str(alice_subject["id"]))
    assert code == 200
    assert body == {"items": []}


# --- admin override --------------------------------------------------------


def test_admin_sees_every_cross_reference(
    client: TestClient,
    admin_token: str,
) -> None:
    """Admins bypass the access filter — all matches are surfaced regardless
    of who owns each matched dossier."""
    _register(client, username="alice", password="alicepassword1")
    _register(client, username="bob", password="bobpassword12")
    alice_token = _login(client, username="alice", password="alicepassword1")
    bob_token = _login(client, username="bob", password="bobpassword12")

    _set_auth(client, alice_token)
    alice_subject = _create(client, "username", "alice")

    _set_auth(client, bob_token)
    bob_subject = _create(client, "username", "alice")

    _set_auth(client, admin_token)
    code, body = _xref(client, str(alice_subject["id"]))
    assert code == 200
    items_value = body["items"]
    assert isinstance(items_value, list)
    matches = {match["id"] for entry in items_value for match in entry["subjects"]}
    assert bob_subject["id"] in matches
    # And the owner_username for the cross-referenced dossier shows the
    # actual creator, not the admin.
    for entry in items_value:
        for match in entry["subjects"]:
            if match["id"] == bob_subject["id"]:
                assert match["owner_username"] == "bob"


def test_admin_sees_legacy_unowned_subject_in_cross_reference(
    client: TestClient,
    admin_token: str,
    api_settings: APISettings,
) -> None:
    """Engine-level CLI saves leave ``subject_owners`` empty; admins must
    still pick those up as cross-references with ``owner_username = null``.
    """
    from datetime import UTC, datetime

    from reckora.evidence.chain import make_evidence
    from reckora.models.entity import Identifier, Subject, Trace
    from reckora.models.enums import IdentifierType, TraceSource
    from reckora.persistence.sqlite import SQLiteSubjectRepository

    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    trace = Trace(
        identifier=seed,
        source=TraceSource.WEB_PROFILE,
        fields={"platform": "fake", "display_name": "alice"},
        evidence=make_evidence(
            "https://fake.example.com/alice",
            {"login": "alice"},
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    legacy = Subject(
        id="subj-legacy00alice",
        seed_identifier=seed,
        identifiers=[seed],
        traces=[trace],
    )
    with SQLiteSubjectRepository(api_settings.db_path) as repo:
        repo.save(subject=legacy, traces=[trace], edges=[])

    _set_auth(client, admin_token)
    admin_subject = _create(client, "username", "alice")

    code, body = _xref(client, str(admin_subject["id"]))
    assert code == 200
    items_value = body["items"]
    assert isinstance(items_value, list)
    legacy_match = next(
        (
            match
            for entry in items_value
            for match in entry["subjects"]
            if match["id"] == "subj-legacy00alice"
        ),
        None,
    )
    assert legacy_match is not None, "admin must see legacy un-owned dossier"
    assert legacy_match["owner_username"] is None


def test_viewer_does_not_see_legacy_unowned_subject_in_cross_reference(
    two_viewer_clients: tuple[TestClient, TestClient],
    api_settings: APISettings,
) -> None:
    """Mirror of the admin case — legacy un-owned rows must NOT leak into a
    viewer's cross-reference response."""
    from datetime import UTC, datetime

    from reckora.evidence.chain import make_evidence
    from reckora.models.entity import Identifier, Subject, Trace
    from reckora.models.enums import IdentifierType, TraceSource
    from reckora.persistence.sqlite import SQLiteSubjectRepository

    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    trace = Trace(
        identifier=seed,
        source=TraceSource.WEB_PROFILE,
        fields={"platform": "fake", "display_name": "alice"},
        evidence=make_evidence(
            "https://fake.example.com/alice",
            {"login": "alice"},
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    legacy = Subject(
        id="subj-legacy00alice",
        seed_identifier=seed,
        identifiers=[seed],
        traces=[trace],
    )
    with SQLiteSubjectRepository(api_settings.db_path) as repo:
        repo.save(subject=legacy, traces=[trace], edges=[])

    alice, _ = two_viewer_clients
    alice_subject = _create(alice, "username", "alice")
    code, body = _xref(alice, str(alice_subject["id"]))
    assert code == 200
    assert body == {"items": []}


# --- ordering --------------------------------------------------------------


def test_xref_orders_matches_newest_first_within_identifier(
    three_viewer_clients: tuple[TestClient, TestClient, TestClient],
    api_settings: APISettings,
) -> None:
    """Within each identifier group, matched dossiers come back ordered by
    ``(created_at DESC, id DESC)`` — the same ordering contract the engine
    repo promises.

    We bypass the API's ``POST /investigations`` path because that uses
    the wall clock and the two subjects can collide on a millisecond,
    making the order non-deterministic. Instead we save two dossiers
    directly through the engine with explicit ``created_at`` values, then
    register matching ownership rows so the API's access filter doesn't
    drop them.
    """
    from datetime import UTC, datetime, timedelta

    from reckora.evidence.chain import make_evidence
    from reckora.models.entity import Identifier, Subject, Trace
    from reckora.models.enums import IdentifierType, TraceSource
    from reckora.persistence.sqlite import SQLiteSubjectRepository

    alice, _bob, _carol = three_viewer_clients
    alice_subject = _create(alice, "username", "alice")

    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    base = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

    older_id = "subj-xref000000a"
    newer_id = "subj-xref000000b"
    for sid, ts in ((older_id, base), (newer_id, base + timedelta(hours=1))):
        trace = Trace(
            identifier=seed,
            source=TraceSource.WEB_PROFILE,
            fields={"platform": "fake", "display_name": "alice"},
            evidence=make_evidence(
                f"https://fake.example.com/alice?ts={ts.isoformat()}",
                {"login": "alice", "ts": ts.isoformat()},
                fetched_at=ts,
            ),
        )
        with SQLiteSubjectRepository(api_settings.db_path) as repo:
            repo.save(
                subject=Subject(
                    id=sid,
                    seed_identifier=seed,
                    identifiers=[seed],
                    traces=[trace],
                ),
                traces=[trace],
                edges=[],
                created_at=ts,
            )

    # Mark both dossiers as alice-owned in the API access tables so the
    # cross-reference query returns them.
    import sqlite3

    with sqlite3.connect(api_settings.db_path) as conn:
        alice_user_id = conn.execute(
            "SELECT id FROM users WHERE username = ?", ("alice",)
        ).fetchone()[0]
        conn.executemany(
            "INSERT INTO subject_owners(subject_id, owner_user_id) VALUES (?, ?)",
            [(older_id, alice_user_id), (newer_id, alice_user_id)],
        )
        conn.commit()

    code, body = _xref(alice, str(alice_subject["id"]))
    assert code == 200
    items_value = body["items"]
    assert isinstance(items_value, list)
    # The two engine-injected subjects should land in the same identifier
    # group (both list the ``username:alice`` seed), with newer first.
    for entry in items_value:
        ids = [m["id"] for m in entry["subjects"]]
        if older_id in ids and newer_id in ids:
            assert ids.index(newer_id) < ids.index(older_id)
            break
    else:  # pragma: no cover - defensive
        pytest.fail("engine-injected subjects missing from cross-reference response")


# --- integrity: no duplicates ----------------------------------------------


def test_xref_does_not_duplicate_a_match_within_identifier_group(
    authed_client: TestClient,
) -> None:
    """The unique index on ``(subject_id, identifier_type, identifier_value)``
    guarantees a matched subject is never listed twice in the same group.
    """
    a = _create(authed_client, "username", "alice")
    b = _create(authed_client, "username", "alice")
    assert b["id"] != a["id"]

    code, body = _xref(authed_client, str(a["id"]))
    assert code == 200
    items_value = body["items"]
    assert isinstance(items_value, list)
    for entry in items_value:
        ids = [m["id"] for m in entry["subjects"]]
        assert len(ids) == len(set(ids)), entry


def test_xref_only_emits_groups_with_at_least_one_visible_match(
    two_viewer_clients: tuple[TestClient, TestClient],
) -> None:
    """An identifier on the source dossier whose only other match is
    invisible to the actor must NOT appear as an empty group — the entire
    entry is dropped.
    """
    alice, bob = two_viewer_clients
    alice_subject = _create(alice, "username", "alice")
    _create(bob, "username", "alice")  # invisible to alice

    code, body = _xref(alice, str(alice_subject["id"]))
    assert code == 200
    assert body == {"items": []}
