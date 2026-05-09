"""Tests for the optional Neo4j :class:`SubjectRepository` adapter.

We never spin up a real Neo4j in CI — the test suite ships a tiny in-memory
fake that implements just enough of the driver / session / transaction
surface to round-trip every call the adapter makes. The fake also dispatches
on a unique substring of each Cypher query, which doubles as a regression
guard: if anyone refactors a query incompatibly, the dispatch falls through
to ``raise AssertionError`` and the test fails loudly instead of silently
no-op-ing.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from reckora.models.entity import Edge, Evidence, Identifier, Subject, Trace
from reckora.models.enums import EdgeKind, IdentifierType, TraceSource
from reckora.persistence import (
    Neo4jSubjectRepository,
    SavedDossier,
    SavedDossierSummary,
    SubjectRepository,
)

# ---------------------------------------------------------------------------
# In-memory fake driver
# ---------------------------------------------------------------------------


@dataclass
class _FakeStore:
    """Shared mutable graph state across sessions."""

    subjects: dict[str, dict[str, Any]] = field(default_factory=dict)
    subject_traces: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    subject_edges: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    subject_identifiers: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    queries: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


class _FakeRecord(dict[str, Any]):
    """Mapping that also supports ``record["key"]`` access (neo4j Record)."""


class _FakeResult:
    def __init__(self, records: list[_FakeRecord]) -> None:
        self._records = records

    def single(self) -> _FakeRecord | None:
        return self._records[0] if self._records else None

    def __iter__(self) -> Iterator[_FakeRecord]:
        return iter(self._records)


_WS = re.compile(r"\s+")


def _norm(query: str) -> str:
    return _WS.sub(" ", query).strip()


class _FakeTransaction:
    def __init__(self, store: _FakeStore) -> None:
        self._store = store

    def run(self, query: str, **params: Any) -> _FakeResult:
        q = _norm(query)
        self._store.queries.append((q, params))

        if q.startswith("CREATE CONSTRAINT"):
            return _FakeResult([])

        if q.startswith("MERGE (sub:Subject {id: $id}) SET sub.seed_kind"):
            self._store.subjects[params["id"]] = {
                "seed_kind": params["seed_kind"],
                "seed_value": params["seed_value"],
                "identifiers_json": params["identifiers_json"],
                "created_at": params["created_at"],
                "summary_md": params["summary"],
                "hypotheses_md": params["hypotheses"],
                "anchor_json": params.get("anchor_json"),
            }
            self._store.subject_traces.setdefault(params["id"], [])
            self._store.subject_edges.setdefault(params["id"], [])
            self._store.subject_identifiers.setdefault(params["id"], [])
            return _FakeResult([])

        if "[:HAS_TRACE]->(t:TraceNode) DETACH DELETE t" in q:
            self._store.subject_traces[params["id"]] = []
            return _FakeResult([])

        if "[:HAS_EDGE]->(e:EdgeNode) DETACH DELETE e" in q:
            self._store.subject_edges[params["id"]] = []
            return _FakeResult([])

        if "[r:HAS_IDENTIFIER]->() DELETE r" in q:
            self._store.subject_identifiers[params["id"]] = []
            return _FakeResult([])

        if "MERGE (sub)-[r:HAS_IDENTIFIER]->(i)" in q:
            self._store.subject_identifiers[params["id"]] = list(params["items"])
            return _FakeResult([])

        if "CREATE (sub)-[:HAS_TRACE]->(:TraceNode" in q:
            self._store.subject_traces[params["id"]] = list(params["items"])
            return _FakeResult([])

        if "CREATE (sub)-[:HAS_EDGE]->(:EdgeNode" in q:
            self._store.subject_edges[params["id"]] = list(params["items"])
            return _FakeResult([])

        if "RETURN count(sub) AS n" in q:
            n = 1 if params["id"] in self._store.subjects else 0
            return _FakeResult([_FakeRecord(n=n)])

        if "DETACH DELETE sub, t, e" in q:
            sid = params["id"]
            self._store.subjects.pop(sid, None)
            self._store.subject_traces.pop(sid, None)
            self._store.subject_edges.pop(sid, None)
            self._store.subject_identifiers.pop(sid, None)
            return _FakeResult([])

        # GET single subject + ordered trace / edge JSON columns
        if "RETURN sub.seed_kind AS seed_kind" in q and "LIMIT" not in q:
            sid = params["id"]
            row = self._store.subjects.get(sid)
            if row is None:
                return _FakeResult([])
            traces = sorted(self._store.subject_traces.get(sid, []), key=lambda t: t["idx"])
            edges = sorted(self._store.subject_edges.get(sid, []), key=lambda e: e["idx"])
            rec = _FakeRecord(
                seed_kind=row["seed_kind"],
                seed_value=row["seed_value"],
                identifiers_json=row["identifiers_json"],
                created_at=row["created_at"],
                summary=row["summary_md"],
                hypotheses=row["hypotheses_md"],
                anchor_json=row.get("anchor_json"),
                traces=[t["trace_json"] for t in traces],
                edges=[e["edge_json"] for e in edges],
            )
            return _FakeResult([rec])

        # LIST subjects sharing an identifier (cross-reference lookup)
        if "(i:Identifier {kind: $kind, value: $value})<-[:HAS_IDENTIFIER]-(sub:Subject)" in q:
            kind = params["kind"]
            value = params["value"]
            exclude = params.get("exclude")
            matched: list[tuple[str, str]] = []
            for sid, items in self._store.subject_identifiers.items():
                if exclude is not None and sid == exclude:
                    continue
                if any(it["kind"] == kind and it["value"] == value for it in items):
                    matched.append((sid, self._store.subjects[sid]["created_at"]))
            matched.sort(key=lambda r: (r[1], r[0]), reverse=True)
            return _FakeResult([_FakeRecord(id=sid, created_at=ts) for sid, ts in matched])

        # LIST recent (paged)
        if "ORDER BY sub.created_at DESC, sub.id DESC LIMIT $limit" in q:
            limit = int(params["limit"])
            ordered = sorted(
                self._store.subjects.items(),
                key=lambda x: (x[1]["created_at"], x[0]),
                reverse=True,
            )
            records = [
                _FakeRecord(
                    id=sid,
                    seed_kind=row["seed_kind"],
                    seed_value=row["seed_value"],
                    identifiers_json=row["identifiers_json"],
                    created_at=row["created_at"],
                    summary_md=row["summary_md"],
                    hypotheses_md=row["hypotheses_md"],
                    anchor_json=row.get("anchor_json"),
                    trace_count=len(self._store.subject_traces.get(sid, [])),
                    edge_count=len(self._store.subject_edges.get(sid, [])),
                )
                for sid, row in ordered[:limit]
            ]
            return _FakeResult(records)

        raise AssertionError(f"unexpected cypher in fake: {q[:120]!r}")


class _FakeSession:
    def __init__(self, store: _FakeStore) -> None:
        self._tx = _FakeTransaction(store)
        self.closed = False

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def run(self, query: str, **params: Any) -> _FakeResult:
        return self._tx.run(query, **params)

    def execute_write(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return fn(self._tx, *args, **kwargs)

    def execute_read(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return fn(self._tx, *args, **kwargs)

    def close(self) -> None:
        self.closed = True


class _FakeDriver:
    def __init__(self) -> None:
        self.store = _FakeStore()
        self.closed = False
        self.session_kwargs: list[dict[str, Any]] = []

    def session(self, **kwargs: Any) -> _FakeSession:
        self.session_kwargs.append(kwargs)
        return _FakeSession(self.store)

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------


def _sample_subject() -> tuple[Subject, list[Trace], list[Edge]]:
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    other = Identifier(type=IdentifierType.EMAIL, value="alice@example.com")
    subject = Subject(
        id="subj-alice",
        seed_identifier=seed,
        identifiers=[seed, other],
    )
    evidence = Evidence(
        source_url="https://example.com/alice",
        fetched_at=datetime(2026, 5, 1, tzinfo=UTC),
        payload_sha256="0" * 64,
    )
    traces = [
        Trace(
            source=TraceSource.GITHUB_API,
            identifier=seed,
            evidence=evidence,
            fields={"display_name": "Alice"},
        )
    ]
    edges = [
        Edge(
            source=seed,
            target=other,
            kind=EdgeKind.SIMILAR_BIO,
            confidence=0.9,
            reasons=["bio_similarity"],
            supporting_evidence=[evidence.payload_sha256],
        )
    ]
    return subject, traces, edges


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_neo4j_repository_satisfies_subject_repository_protocol() -> None:
    """The adapter must be a structural :class:`SubjectRepository`."""
    driver = _FakeDriver()
    repo = Neo4jSubjectRepository(driver)
    assert isinstance(repo, SubjectRepository)


def test_init_creates_constraints_on_construction() -> None:
    driver = _FakeDriver()
    Neo4jSubjectRepository(driver)
    constraints = [q for q, _ in driver.store.queries if q.startswith("CREATE CONSTRAINT")]
    assert any("subject_id_unique" in q for q in constraints)
    assert any("identifier_unique" in q for q in constraints)


def test_save_returns_summary_and_persists_round_trippable_dossier() -> None:
    driver = _FakeDriver()
    repo = Neo4jSubjectRepository(driver)
    subject, traces, edges = _sample_subject()
    created = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

    summary = repo.save(
        subject=subject,
        traces=traces,
        edges=edges,
        summary="Alice ↔ alice@example.com (high confidence).",
        hypotheses="Likely the same operator across two surfaces.",
        created_at=created,
    )

    assert isinstance(summary, SavedDossierSummary)
    assert summary.id == subject.id
    assert summary.identifier_count == 2
    assert summary.trace_count == 1
    assert summary.edge_count == 1
    assert summary.has_summary is True
    assert summary.has_hypotheses is True
    assert summary.created_at == created


def test_save_then_get_round_trip_preserves_models() -> None:
    driver = _FakeDriver()
    repo = Neo4jSubjectRepository(driver)
    subject, traces, edges = _sample_subject()
    repo.save(
        subject=subject,
        traces=traces,
        edges=edges,
        summary="x",
        hypotheses=None,
        created_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
    )

    loaded = repo.get(subject.id)
    assert isinstance(loaded, SavedDossier)
    assert loaded.subject.id == subject.id
    assert loaded.subject.seed_identifier == subject.seed_identifier
    assert [i.value for i in loaded.subject.identifiers] == [
        "alice",
        "alice@example.com",
    ]
    assert loaded.traces[0].fields == {"display_name": "Alice"}
    assert loaded.edges[0].kind == EdgeKind.SIMILAR_BIO
    assert loaded.edges[0].confidence == pytest.approx(0.9)
    assert loaded.summary == "x"
    assert loaded.hypotheses is None


def test_save_is_idempotent_replacing_traces_and_edges() -> None:
    driver = _FakeDriver()
    repo = Neo4jSubjectRepository(driver)
    subject, traces, _edges = _sample_subject()
    repo.save(subject=subject, traces=traces, edges=[])

    # Second save with a single different trace must replace the first.
    new_evidence = Evidence(
        source_url="https://example.com/new",
        fetched_at=datetime(2026, 5, 2, tzinfo=UTC),
        payload_sha256="1" * 64,
    )
    new_trace = Trace(
        source=TraceSource.WEB_PROFILE,
        identifier=subject.seed_identifier,
        evidence=new_evidence,
        fields={"bio": "second"},
    )
    repo.save(subject=subject, traces=[new_trace], edges=[])

    loaded = repo.get(subject.id)
    assert loaded is not None
    assert len(loaded.traces) == 1
    assert loaded.traces[0].source == TraceSource.WEB_PROFILE
    assert loaded.traces[0].fields == {"bio": "second"}


def test_get_unknown_subject_returns_none() -> None:
    driver = _FakeDriver()
    repo = Neo4jSubjectRepository(driver)
    assert repo.get("subj-does-not-exist") is None


def test_list_recent_returns_newest_first_and_respects_limit() -> None:
    driver = _FakeDriver()
    repo = Neo4jSubjectRepository(driver)
    subject, traces, edges = _sample_subject()

    for n, ts in enumerate(
        [
            datetime(2026, 5, 1, tzinfo=UTC),
            datetime(2026, 5, 3, tzinfo=UTC),
            datetime(2026, 5, 2, tzinfo=UTC),
        ]
    ):
        sub = subject.model_copy(update={"id": f"subj-{n}"})
        repo.save(subject=sub, traces=traces, edges=edges, created_at=ts)

    listed = repo.list_recent()
    assert [r.id for r in listed] == ["subj-1", "subj-2", "subj-0"]
    assert listed[0].trace_count == 1
    assert listed[0].edge_count == 1

    assert repo.list_recent(limit=1) == listed[:1]
    assert repo.list_recent(limit=0) == []
    assert repo.list_recent(limit=-5) == []


def test_delete_returns_true_when_row_existed_and_false_otherwise() -> None:
    driver = _FakeDriver()
    repo = Neo4jSubjectRepository(driver)
    subject, traces, edges = _sample_subject()
    repo.save(subject=subject, traces=traces, edges=edges)

    assert repo.delete(subject.id) is True
    assert repo.get(subject.id) is None
    assert repo.delete(subject.id) is False


def test_database_kwarg_is_passed_to_session() -> None:
    driver = _FakeDriver()
    Neo4jSubjectRepository(driver, database="reckora_test")
    assert driver.session_kwargs[0] == {"database": "reckora_test"}


def test_save_identifier_payload_marks_seed_relation() -> None:
    """The HAS_IDENTIFIER edge for the seed identifier sets ``seed = True``."""
    driver = _FakeDriver()
    repo = Neo4jSubjectRepository(driver)
    subject, traces, edges = _sample_subject()
    repo.save(subject=subject, traces=traces, edges=edges)

    seed = subject.seed_identifier
    items = driver.store.subject_identifiers[subject.id]
    seed_items = [i for i in items if i["kind"] == seed.type.value and i["value"] == seed.value]
    assert seed_items, "seed identifier missing from HAS_IDENTIFIER payload"
    assert seed_items[0]["seed"] is True
    assert any(not i["seed"] for i in items)


def test_list_subjects_with_identifier_returns_matches_newest_first() -> None:
    driver = _FakeDriver()
    repo = Neo4jSubjectRepository(driver)
    subject, traces, edges = _sample_subject()
    shared = subject.identifiers[1]  # "alice@example.com"

    older = subject.model_copy(update={"id": "subj-older"})
    newer = subject.model_copy(update={"id": "subj-newer"})
    repo.save(
        subject=older,
        traces=traces,
        edges=edges,
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    repo.save(
        subject=newer,
        traces=traces,
        edges=edges,
        created_at=datetime(2026, 5, 3, tzinfo=UTC),
    )

    matches = repo.list_subjects_with_identifier(shared)
    assert matches == ["subj-newer", "subj-older"]


def test_list_subjects_with_identifier_excludes_source_subject() -> None:
    driver = _FakeDriver()
    repo = Neo4jSubjectRepository(driver)
    subject, traces, edges = _sample_subject()

    twin = subject.model_copy(update={"id": "subj-twin"})
    repo.save(subject=subject, traces=traces, edges=edges)
    repo.save(subject=twin, traces=traces, edges=edges)

    shared = subject.identifiers[0]
    assert repo.list_subjects_with_identifier(shared, exclude_subject_id=subject.id) == [twin.id]


def test_list_subjects_with_identifier_returns_empty_for_unique_identifier() -> None:
    driver = _FakeDriver()
    repo = Neo4jSubjectRepository(driver)
    subject, traces, edges = _sample_subject()
    repo.save(subject=subject, traces=traces, edges=edges)

    novel = Identifier(type=IdentifierType.EMAIL, value="never-seen@example.com")
    assert repo.list_subjects_with_identifier(novel) == []


def test_lazy_import_via_module_getattr() -> None:
    """Importing the symbol from the package must work even though the
    underlying module is loaded lazily.
    """
    import reckora.persistence as persistence

    assert persistence.Neo4jSubjectRepository is Neo4jSubjectRepository

    with pytest.raises(AttributeError):
        _ = persistence.NotARealRepository
