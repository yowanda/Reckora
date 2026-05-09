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

``subject_identifiers``
    composite PK ``(subject_id, identifier_type, identifier_value)`` — a
    flat index of the ``Identifier`` set per subject, used for cheap
    "find every dossier that mentions identifier X" lookups (Phase 5
    cross-references / shared evidence library). The ``identifiers_json``
    column on ``subjects`` is still the source of truth — this table is
    a denormalised view rebuilt on every ``save``.

Each ``Trace`` and ``Edge`` is stored as one canonical-JSON column so we keep
bit-for-bit fidelity with the in-memory Pydantic models. The Pydantic
``model_dump_json`` / ``model_validate_json`` round-trip is what guarantees
field schema stability across releases — bumping a model in a backward
incompatible way will fail validation on read instead of silently corrupting
the dossier.

Foreign-key cascades clean up traces, edges, and identifier-index rows on
subject delete; the ``INSERT OR REPLACE`` in ``save`` therefore keeps
re-runs idempotent without manual child-row cleanup.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from ..evidence.anchor import Anchor
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

CREATE TABLE IF NOT EXISTS dossier_anchors(
    subject_id TEXT PRIMARY KEY REFERENCES subjects(id) ON DELETE CASCADE,
    anchor_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subject_identifiers(
    subject_id TEXT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    identifier_type TEXT NOT NULL,
    identifier_value TEXT NOT NULL,
    PRIMARY KEY (subject_id, identifier_type, identifier_value)
);

CREATE INDEX IF NOT EXISTS idx_subject_identifiers_lookup
    ON subject_identifiers(identifier_type, identifier_value);

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
        self._backfill_subject_identifiers()

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

    def _backfill_subject_identifiers(self) -> None:
        """Populate ``subject_identifiers`` from ``identifiers_json`` for legacy rows.

        Older databases (pre-Phase 5) only carry the JSON column. The
        index table is rebuilt idempotently for every subject that doesn't
        yet have rows in it, so opening such a database for the first time
        with a Phase-5+ build "upgrades" it transparently. Re-runs are a
        no-op once every subject has been backfilled.
        """
        missing = self._conn.execute(
            """
            SELECT s.id, s.identifiers_json
            FROM subjects s
            WHERE NOT EXISTS (
                SELECT 1 FROM subject_identifiers si
                WHERE si.subject_id = s.id
            )
            """
        ).fetchall()
        if not missing:
            return
        rows: list[tuple[str, str, str]] = []
        for sid, identifiers_json in missing:
            for entry in json.loads(identifiers_json):
                rows.append((str(sid), str(entry["type"]), str(entry["value"])))
        if rows:
            with self._tx() as conn:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO subject_identifiers
                        (subject_id, identifier_type, identifier_value)
                    VALUES (?, ?, ?)
                    """,
                    rows,
                )

    def save(
        self,
        *,
        subject: Subject,
        traces: list[Trace],
        edges: list[Edge],
        summary: str | None = None,
        hypotheses: str | None = None,
        anchor: Anchor | None = None,
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
            # ``INSERT OR REPLACE`` already wiped the prior anchor row (FK
            # cascade), but we delete explicitly to cover the
            # save-without-anchor-after-saving-with-anchor case where the
            # cascade does not fire.
            conn.execute("DELETE FROM dossier_anchors WHERE subject_id = ?", (subject.id,))
            if anchor is not None:
                conn.execute(
                    "INSERT INTO dossier_anchors (subject_id, anchor_json) VALUES (?, ?)",
                    (subject.id, anchor.model_dump_json()),
                )
            # Rebuild the flat identifier index. ``INSERT OR REPLACE`` on
            # the parent ``subjects`` row does NOT cascade to children
            # because the row id is unchanged — so we wipe and re-insert
            # explicitly. Deduped by composite PK so a Subject that lists
            # the same identifier twice still produces one row.
            conn.execute(
                "DELETE FROM subject_identifiers WHERE subject_id = ?",
                (subject.id,),
            )
            conn.executemany(
                """
                INSERT OR IGNORE INTO subject_identifiers
                    (subject_id, identifier_type, identifier_value)
                VALUES (?, ?, ?)
                """,
                [(subject.id, i.type.value, i.value) for i in subject.identifiers],
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
            has_anchor=anchor is not None,
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

        anchor_row = self._conn.execute(
            "SELECT anchor_json FROM dossier_anchors WHERE subject_id = ?",
            (subject_id,),
        ).fetchone()
        anchor = Anchor.model_validate_json(anchor_row[0]) if anchor_row is not None else None

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
            anchor=anchor,
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
                   (SELECT COUNT(*) FROM edges e WHERE e.subject_id = s.id),
                   (SELECT COUNT(*) FROM dossier_anchors a WHERE a.subject_id = s.id)
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
            anchor_count,
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
                    has_anchor=int(anchor_count) > 0,
                )
            )
        return out

    def delete(self, subject_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
            return cur.rowcount > 0

    def list_subjects_with_identifier(
        self,
        identifier: Identifier,
        *,
        exclude_subject_id: str | None = None,
    ) -> list[str]:
        """Return the ids of every subject that lists ``identifier``.

        Returns subject ids ordered by ``created_at DESC, id DESC`` so the
        caller can render the most-recent dossiers first without an extra
        sort. Pass ``exclude_subject_id`` to drop a particular subject
        (useful for "other dossiers that mention this identifier").
        """
        if exclude_subject_id is None:
            rows = self._conn.execute(
                """
                SELECT s.id FROM subjects s
                JOIN subject_identifiers si ON si.subject_id = s.id
                WHERE si.identifier_type = ? AND si.identifier_value = ?
                ORDER BY datetime(s.created_at) DESC, s.id DESC
                """,
                (identifier.type.value, identifier.value),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT s.id FROM subjects s
                JOIN subject_identifiers si ON si.subject_id = s.id
                WHERE si.identifier_type = ?
                  AND si.identifier_value = ?
                  AND s.id != ?
                ORDER BY datetime(s.created_at) DESC, s.id DESC
                """,
                (identifier.type.value, identifier.value, exclude_subject_id),
            ).fetchall()
        return [str(r[0]) for r in rows]
