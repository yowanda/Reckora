"""Tests for the SQLite repository — roundtrip, ordering, idempotency, delete."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from reckora.evidence.chain import make_evidence
from reckora.models.entity import Edge, Identifier, Subject, Trace
from reckora.models.enums import EdgeKind, IdentifierType, TraceSource
from reckora.persistence import (
    SavedDossier,
    SavedDossierSummary,
    SQLiteSubjectRepository,
    SubjectRepository,
)


@pytest.fixture
def repo() -> SQLiteSubjectRepository:
    return SQLiteSubjectRepository(":memory:")


@pytest.fixture
def alice_dossier(fixed_now: datetime) -> tuple[Subject, list[Trace], list[Edge]]:
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    extra = Identifier(type=IdentifierType.URL, value="https://example.org/@alice")
    payload_a = {"login": "alice", "bio": "researcher"}
    payload_b = {"title": "alice"}
    trace_a = Trace(
        identifier=seed,
        source=TraceSource.GITHUB_API,
        fields={"platform": "github", "bio": "researcher"},
        evidence=make_evidence(
            "https://api.github.com/users/alice", payload_a, fetched_at=fixed_now
        ),
    )
    trace_b = Trace(
        identifier=extra,
        source=TraceSource.WEB_PROFILE,
        fields={"platform": "example.org", "bio": "researcher"},
        evidence=make_evidence("https://example.org/@alice", payload_b, fetched_at=fixed_now),
    )
    edge = Edge(
        source=seed,
        target=extra,
        kind=EdgeKind.SIMILAR_BIO,
        confidence=0.7,
        reasons=["bio overlap"],
        supporting_evidence=[
            trace_a.evidence.payload_sha256,
            trace_b.evidence.payload_sha256,
        ],
    )
    subject = Subject(
        id="subj-alice000001",
        seed_identifier=seed,
        identifiers=[seed, extra],
        traces=[trace_a, trace_b],
    )
    return subject, [trace_a, trace_b], [edge]


def test_sqlite_repo_satisfies_protocol(repo: SQLiteSubjectRepository) -> None:
    assert isinstance(repo, SubjectRepository)


def test_save_returns_summary(
    repo: SQLiteSubjectRepository,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    subject, traces, edges = alice_dossier
    summary = repo.save(
        subject=subject,
        traces=traces,
        edges=edges,
        summary="hello",
        hypotheses=None,
    )
    assert isinstance(summary, SavedDossierSummary)
    assert summary.id == subject.id
    assert summary.identifier_count == 2
    assert summary.trace_count == 2
    assert summary.edge_count == 1
    assert summary.has_summary is True
    assert summary.has_hypotheses is False


def test_get_roundtrips_dossier(
    repo: SQLiteSubjectRepository,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    subject, traces, edges = alice_dossier
    repo.save(subject=subject, traces=traces, edges=edges, summary="s", hypotheses="h")

    loaded = repo.get(subject.id)
    assert isinstance(loaded, SavedDossier)
    assert loaded.id == subject.id
    assert loaded.subject.seed_identifier == subject.seed_identifier
    assert loaded.subject.identifiers == subject.identifiers
    assert [t.evidence.payload_sha256 for t in loaded.traces] == [
        t.evidence.payload_sha256 for t in traces
    ]
    assert loaded.edges[0].confidence == pytest.approx(edges[0].confidence)
    assert loaded.edges[0].supporting_evidence == edges[0].supporting_evidence
    assert loaded.summary == "s"
    assert loaded.hypotheses == "h"


def test_get_missing_returns_none(repo: SQLiteSubjectRepository) -> None:
    assert repo.get("subj-does-not-exist") is None


def test_save_is_idempotent_for_same_id(
    repo: SQLiteSubjectRepository,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    subject, traces, edges = alice_dossier
    repo.save(subject=subject, traces=traces, edges=edges)
    # second save with fewer traces / edges replaces the previous record
    repo.save(subject=subject, traces=traces[:1], edges=[])

    loaded = repo.get(subject.id)
    assert loaded is not None
    assert len(loaded.traces) == 1
    assert loaded.edges == []


def test_list_recent_orders_newest_first(
    repo: SQLiteSubjectRepository,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    subject, traces, edges = alice_dossier
    older_at = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    newer_at = older_at + timedelta(hours=1)

    older = subject.model_copy(update={"id": "subj-older0000001"})
    newer = subject.model_copy(update={"id": "subj-newer0000001"})
    repo.save(subject=older, traces=traces, edges=edges, created_at=older_at)
    repo.save(subject=newer, traces=traces, edges=edges, created_at=newer_at)

    rows = repo.list_recent(limit=10)
    assert [r.id for r in rows] == [newer.id, older.id]


def test_list_recent_respects_limit(
    repo: SQLiteSubjectRepository,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    subject, traces, edges = alice_dossier
    for i in range(3):
        repo.save(
            subject=subject.model_copy(update={"id": f"subj-{i:014d}"}),
            traces=traces,
            edges=edges,
            created_at=datetime(2026, 1, 1, i, 0, tzinfo=UTC),
        )
    assert len(repo.list_recent(limit=2)) == 2
    assert repo.list_recent(limit=0) == []


def test_delete_returns_true_then_false(
    repo: SQLiteSubjectRepository,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    subject, traces, edges = alice_dossier
    repo.save(subject=subject, traces=traces, edges=edges)
    assert repo.delete(subject.id) is True
    assert repo.delete(subject.id) is False
    assert repo.get(subject.id) is None


def test_delete_cascades_to_traces_and_edges(
    repo: SQLiteSubjectRepository,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    subject, traces, edges = alice_dossier
    repo.save(subject=subject, traces=traces, edges=edges)
    repo.delete(subject.id)

    cur = repo._conn.execute("SELECT COUNT(*) FROM traces WHERE subject_id = ?", (subject.id,))
    assert cur.fetchone()[0] == 0
    cur = repo._conn.execute("SELECT COUNT(*) FROM edges WHERE subject_id = ?", (subject.id,))
    assert cur.fetchone()[0] == 0


def test_file_backed_persists_across_connections(
    tmp_path: Path,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    db_path = tmp_path / "subdir" / "reckora.db"
    subject, traces, edges = alice_dossier
    with SQLiteSubjectRepository(db_path) as repo_a:
        repo_a.save(subject=subject, traces=traces, edges=edges)
    with SQLiteSubjectRepository(db_path) as repo_b:
        loaded = repo_b.get(subject.id)
    assert loaded is not None
    assert loaded.id == subject.id


def test_save_populates_subject_identifiers_index(
    repo: SQLiteSubjectRepository,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    """Each ``Subject.identifiers`` entry shows up in the flat index."""
    subject, traces, edges = alice_dossier
    repo.save(subject=subject, traces=traces, edges=edges)
    rows = repo._conn.execute(
        """
        SELECT identifier_type, identifier_value
        FROM subject_identifiers
        WHERE subject_id = ?
        ORDER BY identifier_type, identifier_value
        """,
        (subject.id,),
    ).fetchall()
    assert sorted((r[0], r[1]) for r in rows) == sorted(
        (i.type.value, i.value) for i in subject.identifiers
    )


def test_save_rebuilds_subject_identifiers_index_on_replace(
    repo: SQLiteSubjectRepository,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    """Re-saving with a different identifier set replaces the old index rows."""
    subject, traces, edges = alice_dossier
    repo.save(subject=subject, traces=traces, edges=edges)

    new_seed = Identifier(type=IdentifierType.EMAIL, value="alice@example.com")
    replaced = subject.model_copy(update={"identifiers": [new_seed]})
    repo.save(subject=replaced, traces=traces, edges=edges)

    rows = repo._conn.execute(
        """
        SELECT identifier_type, identifier_value
        FROM subject_identifiers
        WHERE subject_id = ?
        """,
        (subject.id,),
    ).fetchall()
    assert rows == [(IdentifierType.EMAIL.value, "alice@example.com")]


def test_subject_identifiers_index_dedups_repeated_entries(
    repo: SQLiteSubjectRepository,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    """A Subject that lists the same identifier twice still produces one row."""
    subject, traces, edges = alice_dossier
    seed = subject.seed_identifier
    duplicated = subject.model_copy(update={"identifiers": [seed, seed]})
    repo.save(subject=duplicated, traces=traces, edges=edges)

    cur = repo._conn.execute(
        """
        SELECT COUNT(*) FROM subject_identifiers
        WHERE subject_id = ?
          AND identifier_type = ?
          AND identifier_value = ?
        """,
        (subject.id, seed.type.value, seed.value),
    )
    assert cur.fetchone()[0] == 1


def test_delete_cascades_to_subject_identifiers(
    repo: SQLiteSubjectRepository,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    """``ON DELETE CASCADE`` wipes the index rows when the subject is deleted."""
    subject, traces, edges = alice_dossier
    repo.save(subject=subject, traces=traces, edges=edges)
    repo.delete(subject.id)
    cur = repo._conn.execute(
        "SELECT COUNT(*) FROM subject_identifiers WHERE subject_id = ?",
        (subject.id,),
    )
    assert cur.fetchone()[0] == 0


def test_list_subjects_with_identifier_returns_matches_newest_first(
    repo: SQLiteSubjectRepository,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    """Two subjects sharing one identifier come back ordered ``created_at DESC``."""
    subject, traces, edges = alice_dossier
    shared = subject.identifiers[0]

    older_at = datetime(2026, 5, 1, tzinfo=UTC)
    newer_at = older_at + timedelta(hours=1)
    older = subject.model_copy(update={"id": "subj-older0000000"})
    newer = subject.model_copy(update={"id": "subj-newer0000000"})

    repo.save(subject=older, traces=traces, edges=edges, created_at=older_at)
    repo.save(subject=newer, traces=traces, edges=edges, created_at=newer_at)

    matches = repo.list_subjects_with_identifier(shared)
    assert matches == [newer.id, older.id]


def test_list_subjects_with_identifier_excludes_source_subject(
    repo: SQLiteSubjectRepository,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    subject, traces, edges = alice_dossier
    twin = subject.model_copy(update={"id": "subj-twin0000000"})
    repo.save(subject=subject, traces=traces, edges=edges)
    repo.save(subject=twin, traces=traces, edges=edges)

    shared = subject.identifiers[0]
    matches = repo.list_subjects_with_identifier(shared, exclude_subject_id=subject.id)
    assert matches == [twin.id]


def test_list_subjects_with_identifier_returns_empty_for_unique_identifier(
    repo: SQLiteSubjectRepository,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    subject, traces, edges = alice_dossier
    repo.save(subject=subject, traces=traces, edges=edges)
    novel = Identifier(type=IdentifierType.EMAIL, value="never-seen@example.com")
    assert repo.list_subjects_with_identifier(novel) == []


def test_open_legacy_db_backfills_subject_identifiers_index(
    tmp_path: Path,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    """A pre-Phase-5 database (no index rows) is backfilled when reopened."""
    import sqlite3

    db_path = tmp_path / "legacy.db"
    subject, traces, edges = alice_dossier
    with SQLiteSubjectRepository(db_path) as repo_a:
        repo_a.save(subject=subject, traces=traces, edges=edges)

    # Simulate a legacy DB by wiping the index rows on disk *without* dropping
    # the table — the new repo must re-populate them at construction time.
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM subject_identifiers")
        conn.commit()

    with SQLiteSubjectRepository(db_path) as repo_b:
        rows = repo_b._conn.execute(
            """
            SELECT identifier_type, identifier_value
            FROM subject_identifiers
            WHERE subject_id = ?
            ORDER BY identifier_type, identifier_value
            """,
            (subject.id,),
        ).fetchall()
    assert sorted((r[0], r[1]) for r in rows) == sorted(
        (i.type.value, i.value) for i in subject.identifiers
    )


def test_backfill_is_idempotent_on_repeated_open(
    tmp_path: Path,
    alice_dossier: tuple[Subject, list[Trace], list[Edge]],
) -> None:
    db_path = tmp_path / "reopen.db"
    subject, traces, edges = alice_dossier
    with SQLiteSubjectRepository(db_path) as repo_a:
        repo_a.save(subject=subject, traces=traces, edges=edges)
    # Re-opening the same file twice must not duplicate rows in the index.
    with SQLiteSubjectRepository(db_path) as _repo_b:
        pass
    with SQLiteSubjectRepository(db_path) as repo_c:
        cur = repo_c._conn.execute(
            "SELECT COUNT(*) FROM subject_identifiers WHERE subject_id = ?",
            (subject.id,),
        )
        assert cur.fetchone()[0] == len(subject.identifiers)
