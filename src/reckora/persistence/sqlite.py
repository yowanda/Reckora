"""SQLite implementation of :class:`SubjectRepository`.

Schema (kept deliberately small — the document-shaped fidelity comes from the
JSON columns, the typed columns exist for indexable queries):

``subjects``
    ``id`` (PK), ``seed_kind``, ``seed_value``, ``identifiers_json``,
    ``created_at``, ``summary_md``, ``hypotheses_md``

``traces``
    composite PK ``(subject_id, idx)``, ``trace_json``

``edges``
    composite PK ``(subject_id, idx)``, ``edge_json``

Each ``Trace`` and ``Edge`` is stored as one canonical-JSON column so we keep
bit-for-bit fidelity with the in-memory Pydantic models. The Pydantic
``model_dump_json`` / ``model_validate_json`` round-trip is what guarantees
field schema stability across releases — bumping a model in a backward
incompatible way will fail validation on read instead of silently corrupting
the dossier.

Foreign-key cascades clean up traces and edges on subject delete; the
``INSERT OR REPLACE`` in ``save`` therefore keeps re-runs idempotent without
manual child-row cleanup.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from ..models.entity import Edge, Identifier, Subject, Trace
from ..models.enums import IdentifierType
from .repository import SavedDossier, SavedDossierSummary

_SCHEMA = """
CREATE TABLE IF NOT EXISTS subjects(
    id TEXT PRIMARY KEY,
    seed_kind TEXT NOT NULL,
    seed_value TEXT NOT NULL,
    identifiers_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    summary_md TEXT,
    hypotheses_md TEXT
);

CREATE TABLE IF NOT EXISTS traces(
    subject_id TEXT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    trace_json TEXT NOT NULL,
    PRIMARY KEY (subject_id, idx)
);

CREATE TABLE IF NOT EXISTS edges(
    subject_id TEXT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    edge_json TEXT NOT NULL,
    PRIMARY KEY (subject_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_subjects_created_at_desc
    ON subjects(created_at DESC);
"""


class SQLiteSubjectRepository:
    """File-backed (or in-memory) SQLite repository.

    The repository owns a single connection and serialises writes inside a
    single transaction per ``save`` / ``delete``. Pass ``":memory:"`` for an
    ephemeral in-process database (used by the test-suite).
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # ``check_same_thread=False`` lets the FastAPI worker pool reuse the
        # connection across threads. The repo serialises every write through
        # ``_tx`` and the read paths use ``fetchall()`` with no cursor reuse,
        # so the relaxation is safe under typical low-concurrency loads.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SQLiteSubjectRepository:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def save(
        self,
        *,
        subject: Subject,
        traces: list[Trace],
        edges: list[Edge],
        summary: str | None = None,
        hypotheses: str | None = None,
        created_at: datetime | None = None,
    ) -> SavedDossierSummary:
        ts = (created_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        identifiers_json = json.dumps(
            [i.model_dump(mode="json") for i in subject.identifiers],
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._tx() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO subjects
                    (id, seed_kind, seed_value, identifiers_json,
                     created_at, summary_md, hypotheses_md)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    subject.id,
                    subject.seed_identifier.type.value,
                    subject.seed_identifier.value,
                    identifiers_json,
                    ts,
                    summary,
                    hypotheses,
                ),
            )
            conn.executemany(
                "INSERT INTO traces (subject_id, idx, trace_json) VALUES (?, ?, ?)",
                [(subject.id, i, t.model_dump_json()) for i, t in enumerate(traces)],
            )
            conn.executemany(
                "INSERT INTO edges (subject_id, idx, edge_json) VALUES (?, ?, ?)",
                [(subject.id, i, e.model_dump_json()) for i, e in enumerate(edges)],
            )
        return SavedDossierSummary(
            id=subject.id,
            seed_identifier=subject.seed_identifier,
            created_at=datetime.fromisoformat(ts),
            identifier_count=len(subject.identifiers),
            trace_count=len(traces),
            edge_count=len(edges),
            has_summary=summary is not None,
            has_hypotheses=hypotheses is not None,
        )

    def get(self, subject_id: str) -> SavedDossier | None:
        row = self._conn.execute(
            """
            SELECT seed_kind, seed_value, identifiers_json,
                   created_at, summary_md, hypotheses_md
            FROM subjects WHERE id = ?
            """,
            (subject_id,),
        ).fetchone()
        if row is None:
            return None
        seed_kind, seed_value, identifiers_json, created_at, summary, hypotheses = row
        identifiers = [
            Identifier(type=IdentifierType(d["type"]), value=d["value"])
            for d in json.loads(identifiers_json)
        ]
        seed = Identifier(type=IdentifierType(seed_kind), value=seed_value)

        trace_rows = self._conn.execute(
            "SELECT trace_json FROM traces WHERE subject_id = ? ORDER BY idx",
            (subject_id,),
        ).fetchall()
        traces = [Trace.model_validate_json(r[0]) for r in trace_rows]

        edge_rows = self._conn.execute(
            "SELECT edge_json FROM edges WHERE subject_id = ? ORDER BY idx",
            (subject_id,),
        ).fetchall()
        edges = [Edge.model_validate_json(r[0]) for r in edge_rows]

        subject = Subject(
            id=subject_id,
            seed_identifier=seed,
            identifiers=identifiers,
            traces=traces,
        )
        return SavedDossier(
            id=subject_id,
            subject=subject,
            traces=traces,
            edges=edges,
            created_at=datetime.fromisoformat(created_at),
            summary=summary,
            hypotheses=hypotheses,
        )

    def list_recent(self, limit: int = 20) -> list[SavedDossierSummary]:
        if limit <= 0:
            return []
        rows = self._conn.execute(
            """
            SELECT s.id, s.seed_kind, s.seed_value,
                   s.identifiers_json, s.created_at,
                   s.summary_md, s.hypotheses_md,
                   (SELECT COUNT(*) FROM traces t WHERE t.subject_id = s.id),
                   (SELECT COUNT(*) FROM edges e WHERE e.subject_id = s.id)
            FROM subjects s
            ORDER BY s.created_at DESC, s.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out: list[SavedDossierSummary] = []
        for (
            sid,
            seed_kind,
            seed_value,
            identifiers_json,
            created_at,
            summary_md,
            hypotheses_md,
            trace_count,
            edge_count,
        ) in rows:
            seed = Identifier(type=IdentifierType(seed_kind), value=seed_value)
            ids_data = json.loads(identifiers_json)
            out.append(
                SavedDossierSummary(
                    id=sid,
                    seed_identifier=seed,
                    created_at=datetime.fromisoformat(created_at),
                    identifier_count=len(ids_data),
                    trace_count=int(trace_count),
                    edge_count=int(edge_count),
                    has_summary=summary_md is not None,
                    has_hypotheses=hypotheses_md is not None,
                )
            )
        return out

    def delete(self, subject_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
            return cur.rowcount > 0
