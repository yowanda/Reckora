"""SQLite-backed subject ownership + sharing store.

The access tables live in the same database file as the engine's
``subjects`` table and the API's ``users`` table, which lets us declare
proper ``ON DELETE CASCADE`` foreign keys: deleting a subject (even via
the engine-side CLI) automatically cleans up its owner row and any
shares, and deactivating / hard-deleting a user wipes their shares.

Decoupling from the engine schema
---------------------------------

We deliberately keep ``owner_user_id`` *out of* the ``subjects`` table.
The engine's :class:`~reckora.persistence.repository.SubjectRepository`
doesn't know about users — it can save and load dossiers regardless of
whether the API is ever started. Putting ownership in side tables means
the engine schema stays user-agnostic and the API can evolve its
authorisation model without forcing engine migrations.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import TracebackType

from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType
from reckora.persistence.repository import SavedDossierSummary

_VisibleRow = tuple[str, str, str, str, str, str | None, str | None, int, int, int]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS subject_owners(
    subject_id TEXT PRIMARY KEY
        REFERENCES subjects(id) ON DELETE CASCADE,
    owner_user_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_subject_owners_user
    ON subject_owners(owner_user_id);

CREATE TABLE IF NOT EXISTS subject_shares(
    subject_id TEXT NOT NULL
        REFERENCES subjects(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (subject_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_subject_shares_user
    ON subject_shares(user_id);
"""


class AccessRepository:
    """Owner + share tracking for saved dossiers.

    All public methods are pure book-keeping: they do not synthesise a
    subject or otherwise duplicate engine state. Callers are expected to
    pair these with a :class:`SubjectRepository` for the actual dossier
    payloads.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        # Foreign keys are off by default in SQLite — we rely on cascades to
        # keep owner / share rows tidy when a subject or user is deleted.
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> AccessRepository:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- ownership --------------------------------------------------------

    def set_owner(self, subject_id: str, owner_user_id: int) -> None:
        """Record (or rewrite) the owner for ``subject_id``.

        We use ``INSERT OR REPLACE`` so re-running an investigation that
        produces the same subject id (deterministic seed → stable
        ``subj-...`` hash) re-binds ownership to whoever ran it most
        recently. That matches the existing "last writer wins" semantics
        of :meth:`SubjectRepository.save`.
        """
        self._conn.execute(
            """
            INSERT OR REPLACE INTO subject_owners(subject_id, owner_user_id)
            VALUES (?, ?)
            """,
            (subject_id, owner_user_id),
        )
        self._conn.commit()

    def get_owner(self, subject_id: str) -> int | None:
        row = self._conn.execute(
            "SELECT owner_user_id FROM subject_owners WHERE subject_id = ?",
            (subject_id,),
        ).fetchone()
        if row is None:
            return None
        return int(row[0])

    # -- sharing ----------------------------------------------------------

    def add_share(self, subject_id: str, user_id: int, *, created_at: str) -> bool:
        """Grant ``user_id`` read access to ``subject_id``.

        Returns ``True`` if a new share row was created, ``False`` if the
        user already had access (idempotent). ``created_at`` is recorded
        for audit/UI purposes — we let the caller supply it so tests can
        pin a deterministic timestamp.
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO subject_shares(subject_id, user_id, created_at)
            VALUES (?, ?, ?)
            """,
            (subject_id, user_id, created_at),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def remove_share(self, subject_id: str, user_id: int) -> bool:
        """Revoke an explicit share. Returns ``True`` if a row was removed."""
        cur = self._conn.execute(
            "DELETE FROM subject_shares WHERE subject_id = ? AND user_id = ?",
            (subject_id, user_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_shares(self, subject_id: str) -> list[tuple[int, str]]:
        """Return ``(user_id, created_at)`` pairs for every share of a subject."""
        rows = self._conn.execute(
            """
            SELECT user_id, created_at FROM subject_shares
            WHERE subject_id = ? ORDER BY created_at ASC, user_id ASC
            """,
            (subject_id,),
        ).fetchall()
        return [(int(uid), str(ts)) for uid, ts in rows]

    # -- visibility helpers ----------------------------------------------

    def can_read(self, subject_id: str, user_id: int) -> bool:
        """``True`` if ``user_id`` is the owner or has an explicit share."""
        row = self._conn.execute(
            """
            SELECT 1 WHERE EXISTS(
                SELECT 1 FROM subject_owners
                WHERE subject_id = :sid AND owner_user_id = :uid
            ) OR EXISTS(
                SELECT 1 FROM subject_shares
                WHERE subject_id = :sid AND user_id = :uid
            )
            """,
            {"sid": subject_id, "uid": user_id},
        ).fetchone()
        return row is not None

    def list_visible_summaries(
        self,
        user_id: int,
        *,
        limit: int = 20,
    ) -> list[SavedDossierSummary]:
        """Most-recent dossiers visible to ``user_id`` (owned + shared).

        We hand-roll the query against the engine's ``subjects`` table
        rather than going through :meth:`SubjectRepository.list_recent`
        plus a Python filter, because the latter would require fetching
        an unbounded window to guarantee ``limit`` accurate results when
        the visible set is sparse. The JOIN keeps pagination correct
        without the API loading rows it has to throw away.

        The shape of the resulting :class:`SavedDossierSummary` mirrors
        :meth:`SQLiteSubjectRepository.list_recent` so the API can mix
        admin-flow (engine list) and viewer-flow (this method) rows
        without converting between two representations.
        """
        if limit <= 0:
            return []
        rows: list[_VisibleRow] = self._conn.execute(
            """
            SELECT
                s.id,
                s.seed_kind,
                s.seed_value,
                s.identifiers_json,
                s.created_at,
                s.summary_md,
                s.hypotheses_md,
                COALESCE(
                    (SELECT COUNT(*) FROM traces t WHERE t.subject_id = s.id), 0
                ) AS trace_count,
                COALESCE(
                    (SELECT COUNT(*) FROM edges e WHERE e.subject_id = s.id), 0
                ) AS edge_count,
                COALESCE(
                    (SELECT COUNT(*) FROM dossier_anchors a WHERE a.subject_id = s.id),
                    0
                ) AS anchor_count
            FROM subjects s
            WHERE s.id IN (
                SELECT subject_id FROM subject_owners WHERE owner_user_id = :uid
                UNION
                SELECT subject_id FROM subject_shares  WHERE user_id       = :uid
            )
            ORDER BY datetime(s.created_at) DESC, s.id DESC
            LIMIT :limit
            """,
            {"uid": user_id, "limit": limit},
        ).fetchall()
        return [_row_to_summary(row) for row in rows]


def _row_to_summary(row: _VisibleRow) -> SavedDossierSummary:
    (
        sid,
        seed_kind,
        seed_value,
        identifiers_json,
        created_at,
        summary_md,
        hypotheses_md,
        t,
        e,
        a,
    ) = row
    seed = Identifier(type=IdentifierType(seed_kind), value=seed_value)
    ids_data = json.loads(identifiers_json)
    return SavedDossierSummary(
        id=sid,
        seed_identifier=seed,
        created_at=datetime.fromisoformat(created_at),
        identifier_count=len(ids_data),
        trace_count=int(t),
        edge_count=int(e),
        has_summary=summary_md is not None,
        has_hypotheses=hypotheses_md is not None,
        has_anchor=int(a) > 0,
    )
