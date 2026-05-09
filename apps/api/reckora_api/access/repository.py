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
# (identifier_type, identifier_value,
#  matched_subject_id, matched_seed_kind, matched_seed_value, matched_created_at)
_CrossRefRow = tuple[str, str, str, str, str, str]


@dataclass(frozen=True, slots=True)
class CrossReferenceRow:
    """One ``(shared identifier, matched subject)`` pair.

    A single source dossier produces at most one row per
    ``(matched_subject_id, identifier_type, identifier_value)`` triple.
    Two dossiers that overlap on N identifiers emit N rows; callers
    group by ``(identifier_type, identifier_value)`` to render the
    "this identifier appears in M other dossiers" view.
    """

    identifier_type: str
    identifier_value: str
    matched_subject_id: str
    matched_seed_kind: str
    matched_seed_value: str
    matched_created_at: str


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

CREATE TABLE IF NOT EXISTS subject_comment_replies(
    comment_id INTEGER PRIMARY KEY
        REFERENCES subject_comments(id) ON DELETE CASCADE,
    parent_comment_id INTEGER NOT NULL
        REFERENCES subject_comments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_subject_comment_replies_parent
    ON subject_comment_replies(parent_comment_id);
"""


@dataclass(frozen=True, slots=True)
class CommentRow:
    """One row in :meth:`AccessRepository.list_comments` / :meth:`get_comment`.

    ``parent_comment_id`` is ``None`` for top-level comments and the id
    of the parent comment for replies. Threading is one-level deep —
    a comment that is itself a reply cannot be the parent of another
    reply (the route layer enforces this; the schema permits it but
    no path materialises that state).
    """

    id: int
    subject_id: str
    author_user_id: int
    body: str
    created_at: str
    updated_at: str | None
    parent_comment_id: int | None = None


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

    # -- comments ---------------------------------------------------------

    def add_comment(
        self,
        subject_id: str,
        author_user_id: int,
        body: str,
        *,
        created_at: str,
        parent_comment_id: int | None = None,
    ) -> CommentRow:
        """Append a comment thread entry. Returns the persisted row.

        We materialise the row right after the insert (rather than
        round-tripping ``cur.lastrowid`` only) so the API can hand the
        full :class:`CommentRow` back to the caller without a separate
        SELECT — keeping the create endpoint a single transaction.

        When ``parent_comment_id`` is provided the comment is recorded
        as a reply via the ``subject_comment_replies`` side table. The
        side-table approach lets us add threading without an ALTER on
        the existing ``subject_comments`` schema; absence of a row in
        the side table is the canonical signal that the comment is
        top-level.
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
        comment_id = cur.lastrowid
        if comment_id is None:  # pragma: no cover - sqlite3 contract
            self._conn.rollback()
            raise RuntimeError("INSERT did not yield a lastrowid")
        if parent_comment_id is not None:
            self._conn.execute(
                """
                INSERT INTO subject_comment_replies(
                    comment_id, parent_comment_id
                )
                VALUES (?, ?)
                """,
                (int(comment_id), parent_comment_id),
            )
        self._conn.commit()
        return CommentRow(
            id=int(comment_id),
            subject_id=subject_id,
            author_user_id=author_user_id,
            body=body,
            created_at=created_at,
            updated_at=None,
            parent_comment_id=parent_comment_id,
        )

    def get_comment(self, comment_id: int) -> CommentRow | None:
        """Look up a single comment, used for delete-time authorisation."""
        row = self._conn.execute(
            """
            SELECT
                c.id, c.subject_id, c.author_user_id, c.body,
                c.created_at, c.updated_at,
                r.parent_comment_id
            FROM subject_comments c
            LEFT JOIN subject_comment_replies r ON r.comment_id = c.id
            WHERE c.id = ?
            """,
            (comment_id,),
        ).fetchone()
        if row is None:
            return None
        cid, sid, author_id, body, created_at, updated_at, parent_id = row
        return CommentRow(
            id=int(cid),
            subject_id=str(sid),
            author_user_id=int(author_id),
            body=str(body),
            created_at=str(created_at),
            updated_at=None if updated_at is None else str(updated_at),
            parent_comment_id=None if parent_id is None else int(parent_id),
        )

    def list_comments(self, subject_id: str) -> list[CommentRow]:
        """Return every comment on ``subject_id``, oldest first.

        ``id`` is a deterministic tiebreaker so two comments inserted in
        the same millisecond still come back in insertion order. The
        result includes both top-level comments and replies; the route
        layer is responsible for any tree projection / pagination.
        """
        rows = self._conn.execute(
            """
            SELECT
                c.id, c.subject_id, c.author_user_id, c.body,
                c.created_at, c.updated_at,
                r.parent_comment_id
            FROM subject_comments c
            LEFT JOIN subject_comment_replies r ON r.comment_id = c.id
            WHERE c.subject_id = ?
            ORDER BY datetime(c.created_at) ASC, c.id ASC
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
                parent_comment_id=None if parent_id is None else int(parent_id),
            )
            for cid, sid, author_id, body, created_at, updated_at, parent_id in rows
        ]

    def list_replies(self, parent_comment_id: int) -> list[CommentRow]:
        """Return every reply to ``parent_comment_id``, oldest first.

        Powers the per-thread ``GET /comments/{cid}/replies`` endpoint.
        Caller is responsible for verifying that the parent itself is
        visible to the actor; this method does no authorisation.
        """
        rows = self._conn.execute(
            """
            SELECT
                c.id, c.subject_id, c.author_user_id, c.body,
                c.created_at, c.updated_at,
                r.parent_comment_id
            FROM subject_comment_replies r
            JOIN subject_comments c ON c.id = r.comment_id
            WHERE r.parent_comment_id = ?
            ORDER BY datetime(c.created_at) ASC, c.id ASC
            """,
            (parent_comment_id,),
        ).fetchall()
        return [
            CommentRow(
                id=int(cid),
                subject_id=str(sid),
                author_user_id=int(author_id),
                body=str(body),
                created_at=str(created_at),
                updated_at=None if updated_at is None else str(updated_at),
                parent_comment_id=None if parent_id is None else int(parent_id),
            )
            for cid, sid, author_id, body, created_at, updated_at, parent_id in rows
        ]

    def is_reply(self, comment_id: int) -> bool:
        """``True`` if ``comment_id`` is itself a reply.

        Used by the route layer to enforce one-level threading: the
        parent of a new reply must not itself be a reply.
        """
        row = self._conn.execute(
            "SELECT 1 FROM subject_comment_replies WHERE comment_id = ?",
            (comment_id,),
        ).fetchone()
        return row is not None

    def delete_comment(self, comment_id: int) -> bool:
        """Remove a comment. Returns ``True`` if a row was deleted.

        Replies cascade with the parent: removing a top-level comment
        also wipes every reply pointing at it. The cascade has to be
        applied explicitly here because ``subject_comment_replies``
        only declares ``ON DELETE CASCADE`` on its own row (which
        clears the join table when the parent vanishes), not on the
        reply comment itself in ``subject_comments``.

        The one-level threading rule means we do not need to recurse
        \u2014 a reply cannot itself have replies, so a single sweep is
        always sufficient.
        """
        self._conn.execute(
            """
            DELETE FROM subject_comments
            WHERE id IN (
                SELECT comment_id
                FROM subject_comment_replies
                WHERE parent_comment_id = ?
            )
            """,
            (comment_id,),
        )
        cur = self._conn.execute(
            "DELETE FROM subject_comments WHERE id = ?",
            (comment_id,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def update_comment(
        self,
        comment_id: int,
        body: str,
        *,
        updated_at: str,
    ) -> CommentRow | None:
        """Replace a comment's body and stamp ``updated_at``.

        Returns the freshly-updated row, or ``None`` if no comment with
        ``comment_id`` exists. The caller is responsible for the
        authorisation decision (only the comment author should be able
        to edit) and for validating the body — we do the minimum
        ``UPDATE`` here so the repository stays a thin SQL wrapper.

        We deliberately do NOT touch ``created_at``: clients render
        edits with a "(edited)" badge by checking
        ``updated_at is not None``, so ``created_at`` must remain
        the original anchor.
        """
        cur = self._conn.execute(
            """
            UPDATE subject_comments
            SET body = ?, updated_at = ?
            WHERE id = ?
            """,
            (body, updated_at, comment_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            return None
        return self.get_comment(comment_id)

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

    def list_cross_references(
        self,
        source_subject_id: str,
        *,
        user_id: int,
        is_admin: bool,
    ) -> list[CrossReferenceRow]:
        """List ``(shared identifier, matched subject)`` rows for ``source_subject_id``.

        For each identifier on the source dossier, return every *other*
        subject that lists the same identifier *and* is visible to the
        actor:

        - **Admins** see every match (including legacy un-owned dossiers
          created by the CLI).
        - **Viewers** see matches they own or have explicitly been
          shared.

        Rows are ordered by identifier (type then value, deterministic
        across calls), then by ``created_at DESC, id DESC`` within each
        identifier group, so the API can stream them straight into a
        grouped response without an in-memory re-sort.

        We materialise this against the engine's
        :class:`reckora.persistence.sqlite.SQLiteSubjectRepository`'s
        ``subject_identifiers`` index (added in Phase 5) — without that
        denormalised table the query would have to JSON-scan every
        ``identifiers_json`` blob.
        """
        if is_admin:
            rows: list[_CrossRefRow] = self._conn.execute(
                """
                SELECT
                    si_other.identifier_type,
                    si_other.identifier_value,
                    other.id,
                    other.seed_kind,
                    other.seed_value,
                    other.created_at
                FROM subject_identifiers si_source
                JOIN subject_identifiers si_other
                    ON  si_other.identifier_type  = si_source.identifier_type
                    AND si_other.identifier_value = si_source.identifier_value
                    AND si_other.subject_id      != si_source.subject_id
                JOIN subjects other ON other.id = si_other.subject_id
                WHERE si_source.subject_id = :source
                ORDER BY
                    si_other.identifier_type ASC,
                    si_other.identifier_value ASC,
                    datetime(other.created_at) DESC,
                    other.id DESC
                """,
                {"source": source_subject_id},
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT
                    si_other.identifier_type,
                    si_other.identifier_value,
                    other.id,
                    other.seed_kind,
                    other.seed_value,
                    other.created_at
                FROM subject_identifiers si_source
                JOIN subject_identifiers si_other
                    ON  si_other.identifier_type  = si_source.identifier_type
                    AND si_other.identifier_value = si_source.identifier_value
                    AND si_other.subject_id      != si_source.subject_id
                JOIN subjects other ON other.id = si_other.subject_id
                WHERE si_source.subject_id = :source
                  AND (
                      EXISTS (
                          SELECT 1 FROM subject_owners o
                          WHERE o.subject_id = other.id AND o.owner_user_id = :uid
                      )
                      OR EXISTS (
                          SELECT 1 FROM subject_shares sh
                          WHERE sh.subject_id = other.id AND sh.user_id = :uid
                      )
                  )
                ORDER BY
                    si_other.identifier_type ASC,
                    si_other.identifier_value ASC,
                    datetime(other.created_at) DESC,
                    other.id DESC
                """,
                {"source": source_subject_id, "uid": user_id},
            ).fetchall()
        return [
            CrossReferenceRow(
                identifier_type=str(itype),
                identifier_value=str(ivalue),
                matched_subject_id=str(sid),
                matched_seed_kind=str(seed_kind),
                matched_seed_value=str(seed_value),
                matched_created_at=str(created_at),
            )
            for itype, ivalue, sid, seed_kind, seed_value, created_at in rows
        ]

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
