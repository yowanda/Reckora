"""SQLite-backed subject ownership / sharing / collaboration store.

The access tables live in the same database file as the engine's
``subjects`` table and the API's ``users`` table, which lets us declare
proper ``ON DELETE CASCADE`` foreign keys: deleting a subject (even via
the engine-side CLI) automatically cleans up its owner row, any shares,
any comments, and any assignment rows; deactivating / hard-deleting a
user wipes their shares, their authored comments, and their assignment
rows, while preserving the ``assigned_by`` audit trail on rows the
deleted user *granted* (those collapse to ``NULL`` rather than
vanishing).

Decoupling from the engine schema
---------------------------------

We deliberately keep ``owner_user_id`` *out of* the ``subjects`` table.
The engine's :class:`~reckora.persistence.repository.SubjectRepository`
doesn't know about users — it can save and load dossiers regardless of
whether the API is ever started. Putting ownership in side tables means
the engine schema stays user-agnostic and the API can evolve its
authorisation model (sharing, assignment, comments, …) without forcing
engine migrations.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
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

CREATE TABLE IF NOT EXISTS subject_comments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id TEXT NOT NULL
        REFERENCES subjects(id) ON DELETE CASCADE,
    author_user_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_subject_comments_subject
    ON subject_comments(subject_id, created_at, id);

CREATE INDEX IF NOT EXISTS idx_subject_comments_author
    ON subject_comments(author_user_id);

CREATE TABLE IF NOT EXISTS subject_assignees(
    subject_id TEXT NOT NULL
        REFERENCES subjects(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    assigned_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,
    assigned_at TEXT NOT NULL,
    PRIMARY KEY (subject_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_subject_assignees_user
    ON subject_assignees(user_id);

CREATE TABLE IF NOT EXISTS subject_visits(
    subject_id TEXT NOT NULL
        REFERENCES subjects(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (subject_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_subject_visits_user
    ON subject_visits(user_id, last_seen_at, subject_id);
"""


@dataclass(frozen=True, slots=True)
class CommentRow:
    """One row in :meth:`AccessRepository.list_comments` / :meth:`get_comment`."""

    id: int
    subject_id: str
    author_user_id: int
    body: str
    created_at: str
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class AssigneeRow:
    """One row in :meth:`AccessRepository.list_assignees`."""

    subject_id: str
    user_id: int
    assigned_by: int | None
    assigned_at: str


class AccessRepository:
    """Owner / share / assignment / comment book-keeping for saved dossiers.

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
        # keep owner / share / comment / assignment rows tidy when a
        # subject or user is deleted.
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

    # -- assignment -------------------------------------------------------

    def add_assignee(
        self,
        subject_id: str,
        user_id: int,
        *,
        assigned_by: int | None,
        assigned_at: str,
    ) -> bool:
        """Assign ``user_id`` to ``subject_id`` (idempotent).

        Returns ``True`` when a new row was inserted, ``False`` if the
        user was already assigned. Assignment is independent of sharing
        — see :meth:`can_read`, which treats both as read-grants.

        ``assigned_by`` may be ``None`` so callers can record
        system-driven assignments (e.g. an automated triage worker)
        that don't map to a single human. The column is also nullable
        on disk so that deleting the granting user doesn't cascade-erase
        the assignment itself.
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO subject_assignees(
                subject_id, user_id, assigned_by, assigned_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (subject_id, user_id, assigned_by, assigned_at),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def remove_assignee(self, subject_id: str, user_id: int) -> bool:
        """Unassign a user. Returns ``True`` if a row was removed."""
        cur = self._conn.execute(
            "DELETE FROM subject_assignees WHERE subject_id = ? AND user_id = ?",
            (subject_id, user_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_assignees(self, subject_id: str) -> list[AssigneeRow]:
        """Return every assignment row for ``subject_id``, oldest first."""
        rows = self._conn.execute(
            """
            SELECT subject_id, user_id, assigned_by, assigned_at
            FROM subject_assignees
            WHERE subject_id = ?
            ORDER BY assigned_at ASC, user_id ASC
            """,
            (subject_id,),
        ).fetchall()
        return [
            AssigneeRow(
                subject_id=str(sid),
                user_id=int(uid),
                assigned_by=None if granted_by is None else int(granted_by),
                assigned_at=str(ts),
            )
            for sid, uid, granted_by, ts in rows
        ]

    def is_assigned(self, subject_id: str, user_id: int) -> bool:
        """Cheap existence probe used by :meth:`can_read`."""
        row = self._conn.execute(
            """
            SELECT 1 FROM subject_assignees
            WHERE subject_id = ? AND user_id = ?
            """,
            (subject_id, user_id),
        ).fetchone()
        return row is not None

    # -- per-actor visit stamps ------------------------------------------

    def mark_visited(
        self,
        subject_id: str,
        user_id: int,
        *,
        now: str,
    ) -> str:
        """Stamp ``user_id``'s last-seen marker on ``subject_id`` to ``now``.

        The stamp always advances on write — a second POST in the
        same wall-clock second is harmless (both bumps land on the
        same value), but a stale stamp is never preserved.
        """
        self._conn.execute(
            """
            INSERT INTO subject_visits(subject_id, user_id, last_seen_at)
            VALUES (?, ?, ?)
            ON CONFLICT (subject_id, user_id) DO UPDATE SET
                last_seen_at = excluded.last_seen_at
            """,
            (subject_id, user_id, now),
        )
        self._conn.commit()
        return now

    def get_last_visit(
        self,
        subject_id: str,
        user_id: int,
    ) -> str | None:
        """Return the actor's last-seen ISO timestamp, or ``None``."""
        row = self._conn.execute(
            """
            SELECT last_seen_at FROM subject_visits
            WHERE subject_id = ? AND user_id = ?
            """,
            (subject_id, user_id),
        ).fetchone()
        return None if row is None else str(row[0])

    def count_unread_comments(
        self,
        subject_id: str,
        user_id: int,
    ) -> int:
        """Count comments on ``subject_id`` newer than ``user_id``'s visit.

        Semantics:

        * Never visited → *every* comment counts as unread, so a
          freshly-shared collaborator sees a non-zero badge that
          motivates them to open the dossier.
        * Visited → only comments strictly newer than the stamp
          are counted. Comments authored by the actor themselves
          are *not* excluded — a comment posted at T+1 shows up as
          "unread" until the actor next visits, which mirrors how
          chat clients badge your own messages until you scroll
          past them.
        """
        last_seen = self.get_last_visit(subject_id, user_id)
        if last_seen is None:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM subject_comments WHERE subject_id = ?",
                (subject_id,),
            ).fetchone()
        else:
            row = self._conn.execute(
                """
                SELECT COUNT(*) FROM subject_comments
                WHERE subject_id = ? AND created_at > ?
                """,
                (subject_id, last_seen),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    # -- comments ---------------------------------------------------------

    def add_comment(
        self,
        subject_id: str,
        author_user_id: int,
        body: str,
        *,
        created_at: str,
    ) -> CommentRow:
        """Append a comment thread entry. Returns the persisted row.

        We materialise the row right after the insert (rather than
        round-tripping ``cur.lastrowid`` only) so the API can hand the
        full :class:`CommentRow` back to the caller without a separate
        SELECT — keeping the create endpoint a single transaction.
        """
        cur = self._conn.execute(
            """
            INSERT INTO subject_comments(
                subject_id, author_user_id, body, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, NULL)
            """,
            (subject_id, author_user_id, body, created_at),
        )
        self._conn.commit()
        comment_id = cur.lastrowid
        if comment_id is None:  # pragma: no cover - sqlite3 contract
            raise RuntimeError("INSERT did not yield a lastrowid")
        return CommentRow(
            id=int(comment_id),
            subject_id=subject_id,
            author_user_id=author_user_id,
            body=body,
            created_at=created_at,
            updated_at=None,
        )

    def get_comment(self, comment_id: int) -> CommentRow | None:
        """Look up a single comment, used for delete-time authorisation."""
        row = self._conn.execute(
            """
            SELECT id, subject_id, author_user_id, body, created_at, updated_at
            FROM subject_comments
            WHERE id = ?
            """,
            (comment_id,),
        ).fetchone()
        if row is None:
            return None
        cid, sid, author_id, body, created_at, updated_at = row
        return CommentRow(
            id=int(cid),
            subject_id=str(sid),
            author_user_id=int(author_id),
            body=str(body),
            created_at=str(created_at),
            updated_at=None if updated_at is None else str(updated_at),
        )

    def list_comments(self, subject_id: str) -> list[CommentRow]:
        """Return every comment on ``subject_id``, oldest first.

        ``id`` is a deterministic tiebreaker so two comments inserted in
        the same millisecond still come back in insertion order.
        """
        rows = self._conn.execute(
            """
            SELECT id, subject_id, author_user_id, body, created_at, updated_at
            FROM subject_comments
            WHERE subject_id = ?
            ORDER BY datetime(created_at) ASC, id ASC
            """,
            (subject_id,),
        ).fetchall()
        return [
            CommentRow(
                id=int(cid),
                subject_id=str(sid),
                author_user_id=int(author_id),
                body=str(body),
                created_at=str(created_at),
                updated_at=None if updated_at is None else str(updated_at),
            )
            for cid, sid, author_id, body, created_at, updated_at in rows
        ]

    def delete_comment(self, comment_id: int) -> bool:
        """Remove a comment. Returns ``True`` if a row was deleted."""
        cur = self._conn.execute(
            "DELETE FROM subject_comments WHERE id = ?",
            (comment_id,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # -- visibility helpers ----------------------------------------------

    def can_read(self, subject_id: str, user_id: int) -> bool:
        """``True`` if ``user_id`` is the owner, has a share, or is assigned.

        Assignment grants implicit read access — there is no scenario
        where a user should be tasked with working on a dossier but
        unable to open it. Callers that need to distinguish *why* a
        user can read should compose ``get_owner`` / ``list_shares`` /
        ``list_assignees`` themselves.
        """
        row = self._conn.execute(
            """
            SELECT 1 WHERE EXISTS(
                SELECT 1 FROM subject_owners
                WHERE subject_id = :sid AND owner_user_id = :uid
            ) OR EXISTS(
                SELECT 1 FROM subject_shares
                WHERE subject_id = :sid AND user_id = :uid
            ) OR EXISTS(
                SELECT 1 FROM subject_assignees
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
        """Most-recent dossiers visible to ``user_id`` (owned, shared, or assigned).

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
                SELECT subject_id FROM subject_owners    WHERE owner_user_id = :uid
                UNION
                SELECT subject_id FROM subject_shares    WHERE user_id       = :uid
                UNION
                SELECT subject_id FROM subject_assignees WHERE user_id       = :uid
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
