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

CREATE TABLE IF NOT EXISTS comment_reactions(
    comment_id INTEGER NOT NULL
        REFERENCES subject_comments(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    reaction_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (comment_id, user_id, reaction_key)
);

CREATE INDEX IF NOT EXISTS idx_comment_reactions_comment
    ON comment_reactions(comment_id);

CREATE INDEX IF NOT EXISTS idx_comment_reactions_user
    ON comment_reactions(user_id);

CREATE TABLE IF NOT EXISTS subject_labels(
    subject_id TEXT NOT NULL
        REFERENCES subjects(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    created_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (subject_id, label)
);

CREATE INDEX IF NOT EXISTS idx_subject_labels_label
    ON subject_labels(label);

CREATE TABLE IF NOT EXISTS subject_status(
    subject_id TEXT PRIMARY KEY
        REFERENCES subjects(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    updated_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_subject_status_status
    ON subject_status(status);

CREATE TABLE IF NOT EXISTS subject_watchers(
    subject_id TEXT NOT NULL
        REFERENCES subjects(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (subject_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_subject_watchers_user
    ON subject_watchers(user_id, created_at, subject_id);

CREATE TABLE IF NOT EXISTS subject_comment_mentions(
    comment_id INTEGER NOT NULL
        REFERENCES subject_comments(id) ON DELETE CASCADE,
    mentioned_user_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (comment_id, mentioned_user_id)
);

CREATE INDEX IF NOT EXISTS idx_subject_comment_mentions_user
    ON subject_comment_mentions(mentioned_user_id, created_at, comment_id);

CREATE TABLE IF NOT EXISTS subject_pins(
    subject_id TEXT NOT NULL
        REFERENCES subjects(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    pinned_at TEXT NOT NULL,
    PRIMARY KEY (subject_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_subject_pins_user
    ON subject_pins(user_id, pinned_at, subject_id);

CREATE TABLE IF NOT EXISTS subject_notes(
    subject_id TEXT NOT NULL
        REFERENCES subjects(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subject_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_subject_notes_user
    ON subject_notes(user_id, updated_at, subject_id);

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


@dataclass(frozen=True, slots=True)
class ActivityRow:
    """One event in :meth:`AccessRepository.list_activity`.

    The activity feed is a chronological union over the four tables that
    record observable mutations on a saved dossier: comments, assignees,
    explicit shares, and the cross-trace anchor mint. We deliberately do
    NOT model role flips or ownership transfers here — those live in
    different layers and have their own audit story.

    Field semantics
    ---------------

    * ``kind`` — one of ``"comment_added"``, ``"assigned"``, ``"shared"``,
      ``"anchored"``. Stable strings so the API can wire-protocol them
      without re-keying.
    * ``actor_user_id`` — who *caused* the event:
        - ``comment_added``: the comment author.
        - ``assigned``: the user who granted the assignment
          (``subject_assignees.assigned_by``); may be ``None`` if that
          user has since been deleted (the row survives via
          ``ON DELETE SET NULL``).
        - ``shared``: ``None`` — the share schema does not yet record a
          granter; we surface the row anyway so the feed reflects access
          changes, but the actor column is intentionally blank.
        - ``anchored``: ``None`` — the anchor is minted by the engine,
          not a specific user.
    * ``target_user_id`` — the user the event is *about*, when one
      exists:
        - ``assigned`` / ``shared``: the user gaining access.
        - ``comment_added`` / ``anchored``: ``None``.
    * ``excerpt`` — for ``comment_added``, a leading slice of the comment
      body (max 200 chars) so the feed renders without a second
      round-trip; ``None`` for the other kinds.
    * ``created_at`` — ISO-8601 timestamp from the underlying row. For
      ``anchored`` we pull from the parent subject row because the
      ``dossier_anchors`` table does not store a timestamp of its own.
    """

    kind: str
    actor_user_id: int | None
    target_user_id: int | None
    excerpt: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class ReactionRow:
    """One row in :meth:`AccessRepository.list_reactions`.

    A row models a single ``(comment, user, reaction_key)`` triple —
    the same user is allowed to leave multiple distinct reactions on
    one comment (e.g. ``+1`` *and* ``heart``) but cannot stack the
    same key twice. The route layer is responsible for projecting
    these into the ``[{key, count, users[], me_reacted}]`` summary
    surfaced to the API; the repository deliberately stays at the
    row-level granularity so other consumers (e.g. an audit export)
    can pivot however they need.
    """

    comment_id: int
    user_id: int
    reaction_key: str
    created_at: str


@dataclass(frozen=True, slots=True)
class LabelRow:
    """One row in :meth:`AccessRepository.list_labels`.

    ``created_by`` is nullable because the column carries
    ``ON DELETE SET NULL``: a labeller's account being removed
    preserves the audit trail of *what* labels exist while collapsing
    *who* applied them. Labels themselves are normalised to lower-case
    by the route layer before insert so ``OSINT`` and ``osint`` collapse
    to a single row — consistent with how most issue trackers handle
    tags.
    """

    subject_id: str
    label: str
    created_by: int | None
    created_at: str


@dataclass(frozen=True, slots=True)
class StatusRow:
    """One row in :meth:`AccessRepository.get_status`.

    Each subject has at most one status row; the absence of a row
    means the dossier is in the implicit "open" state. We store the
    explicit row only when somebody changes the status away from
    open, *or* sets it back to open after a previous transition —
    that way the audit trail (``updated_at`` / ``updated_by``) is
    preserved even on a return-to-open.
    """

    subject_id: str
    status: str
    updated_by: int | None
    updated_at: str


@dataclass(frozen=True, slots=True)
class WatcherRow:
    """One row in :meth:`AccessRepository.list_watchers`.

    A watcher is a self-subscribed reader who has opted in to follow
    a dossier. Watchers are independent of ownership / sharing /
    assignment: revoking a share also wipes the watch (cascade), but
    you can be a watcher without being assigned.
    """

    subject_id: str
    user_id: int
    created_at: str


@dataclass(frozen=True, slots=True)
class MentionRow:
    """One row in :meth:`AccessRepository.list_mentions_for_user`.

    Joins ``subject_comment_mentions`` against ``subject_comments`` so
    the route layer has every field the per-actor mentions feed
    needs: when the mention happened, where it lives, and which
    user authored the comment.
    """

    comment_id: int
    subject_id: str
    author_user_id: int
    body: str
    comment_created_at: str
    mention_created_at: str


@dataclass(frozen=True, slots=True)
class NoteRow:
    """One row in the per-actor :meth:`AccessRepository.get_note`.

    Notes are private to ``user_id`` — a different actor reading
    the same subject sees their own row (or no row at all).
    """

    subject_id: str
    user_id: int
    body: str
    created_at: str
    updated_at: str


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

    # -- watchers ---------------------------------------------------------

    def add_watcher(
        self,
        subject_id: str,
        user_id: int,
        *,
        created_at: str,
    ) -> bool:
        """Record that ``user_id`` is following ``subject_id`` (idempotent).

        Returns ``True`` when a new row was inserted, ``False`` if the
        user was already a watcher. The route layer treats both cases
        as a successful 200 so an optimistic UI never has to special-case
        the second click on the bell icon.

        Watching is *not* a read grant — callers must verify
        :meth:`can_read` (or admin) before exposing this method, the
        same way the comments / labels / status endpoints gate writes.
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO subject_watchers(
                subject_id, user_id, created_at
            )
            VALUES (?, ?, ?)
            """,
            (subject_id, user_id, created_at),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def remove_watcher(self, subject_id: str, user_id: int) -> bool:
        """Stop following a dossier. Returns ``True`` if a row was removed."""
        cur = self._conn.execute(
            "DELETE FROM subject_watchers WHERE subject_id = ? AND user_id = ?",
            (subject_id, user_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_watchers(self, subject_id: str) -> list[WatcherRow]:
        """Return every watcher of ``subject_id``, oldest first.

        ``user_id`` is the deterministic tiebreaker so two subscribes
        in the same millisecond still come back in the same order on
        every request — important for the UI's avatar stack.
        """
        # ISO-8601 timestamps sort lexicographically, and string
        # ordering preserves the microsecond precision that
        # ``datetime()`` (second-precision only) would discard — vital
        # for the test that subscribes twice in the same wall second
        # to deterministically come back in subscription order.
        rows = self._conn.execute(
            """
            SELECT subject_id, user_id, created_at
            FROM subject_watchers
            WHERE subject_id = ?
            ORDER BY created_at ASC, user_id ASC
            """,
            (subject_id,),
        ).fetchall()
        return [
            WatcherRow(
                subject_id=str(sid),
                user_id=int(uid),
                created_at=str(ts),
            )
            for sid, uid, ts in rows
        ]

    def is_watching(self, subject_id: str, user_id: int) -> bool:
        """Cheap existence probe used by the per-dossier endpoint."""
        row = self._conn.execute(
            """
            SELECT 1 FROM subject_watchers
            WHERE subject_id = ? AND user_id = ?
            """,
            (subject_id, user_id),
        ).fetchone()
        return row is not None

    def list_watched_summaries(
        self,
        user_id: int,
        *,
        limit: int = 50,
    ) -> list[SavedDossierSummary]:
        """Most-recently-watched dossiers belonging to ``user_id``.

        Ordered by *subscription* time (most-recent watch first), not
        by dossier creation time — the UI surfaces this as "My
        watchlist", and the obvious user expectation is that the
        last thing you starred sits at the top.

        We re-use the visibility-aware shape of
        :meth:`list_visible_summaries` so admin-flow and watch-flow
        rows mix without conversion. Watching is gated behind
        :meth:`can_read` at the route layer; we therefore don't
        re-filter for visibility here — a watch row whose underlying
        share / assignment was revoked simply hangs around until the
        cascade fires (or the user explicitly un-watches).
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
            JOIN subject_watchers w ON w.subject_id = s.id
            WHERE w.user_id = :uid
            ORDER BY w.created_at DESC, s.id DESC
            LIMIT :limit
            """,
            {"uid": user_id, "limit": limit},
        ).fetchall()
        return [_row_to_summary(row) for row in rows]

    # -- mentions ---------------------------------------------------------

    def add_mention(
        self,
        comment_id: int,
        mentioned_user_id: int,
        *,
        created_at: str,
    ) -> bool:
        """Record that ``comment_id`` mentions ``mentioned_user_id``.

        Idempotent: re-mentioning the same user (e.g. on an edited
        comment that adds a duplicate ``@username``) is a no-op. The
        primary key on ``(comment_id, mentioned_user_id)`` collapses
        the duplicate so the per-actor feed never surfaces the same
        comment twice.
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO subject_comment_mentions(
                comment_id, mentioned_user_id, created_at
            )
            VALUES (?, ?, ?)
            """,
            (comment_id, mentioned_user_id, created_at),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_mentions_for_comment(self, comment_id: int) -> list[int]:
        """Return ``mentioned_user_id`` for every mention on a comment.

        Used by the comments routes to populate the ``mentions``
        field on the wire response — the route layer joins this
        against the user table to surface usernames.
        """
        rows = self._conn.execute(
            """
            SELECT mentioned_user_id FROM subject_comment_mentions
            WHERE comment_id = ?
            ORDER BY mentioned_user_id ASC
            """,
            (comment_id,),
        ).fetchall()
        return [int(uid) for (uid,) in rows]

    def list_mentions_for_user(
        self,
        user_id: int,
        *,
        limit: int = 50,
    ) -> list[MentionRow]:
        """Return the ``user_id``'s mention feed, most-recent first.

        The feed is per-actor and crosses dossiers — a mention from
        any subject the user can read shows up here. We do *not*
        filter by current visibility: a mention emitted while the
        user had access stays in the feed even after their share is
        revoked. This is the same trade-off comments make for the
        thread itself.
        """
        if limit <= 0:
            return []
        rows = self._conn.execute(
            """
            SELECT
                m.comment_id,
                c.subject_id,
                c.author_user_id,
                c.body,
                c.created_at,
                m.created_at
            FROM subject_comment_mentions m
            JOIN subject_comments c ON c.id = m.comment_id
            WHERE m.mentioned_user_id = :uid
            ORDER BY m.created_at DESC, m.comment_id DESC
            LIMIT :limit
            """,
            {"uid": user_id, "limit": limit},
        ).fetchall()
        return [
            MentionRow(
                comment_id=int(cid),
                subject_id=str(sid),
                author_user_id=int(author_id),
                body=str(body),
                comment_created_at=str(comment_ts),
                mention_created_at=str(mention_ts),
            )
            for cid, sid, author_id, body, comment_ts, mention_ts in rows
        ]

    # -- pins (per-actor favourites) -------------------------------------

    def add_pin(
        self,
        subject_id: str,
        user_id: int,
        *,
        pinned_at: str,
    ) -> bool:
        """Mark ``subject_id`` as pinned for ``user_id``.

        Idempotent — re-pinning is a no-op rather than refreshing the
        timestamp. The route layer relies on this so a double-tap of
        the pin button does not silently re-order the favourites
        list out from under the user.
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO subject_pins(subject_id, user_id, pinned_at)
            VALUES (?, ?, ?)
            """,
            (subject_id, user_id, pinned_at),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def remove_pin(self, subject_id: str, user_id: int) -> bool:
        """Drop ``user_id``'s pin on ``subject_id``. Idempotent on absent rows."""
        cur = self._conn.execute(
            "DELETE FROM subject_pins WHERE subject_id = ? AND user_id = ?",
            (subject_id, user_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def is_pinned(self, subject_id: str, user_id: int) -> bool:
        """Cheap existence probe used by route handlers."""
        row = self._conn.execute(
            """
            SELECT 1 FROM subject_pins
            WHERE subject_id = ? AND user_id = ?
            """,
            (subject_id, user_id),
        ).fetchone()
        return row is not None

    def list_pinned_summaries(
        self,
        user_id: int,
        *,
        limit: int = 50,
    ) -> list[SavedDossierSummary]:
        """Return ``user_id``'s pinned dossiers, most-recently-pinned first.

        We INNER JOIN against the visibility set rather than just
        ``subject_pins`` so a pin for a dossier the user has lost
        access to (e.g. share revoked) is *silently filtered* from
        the wire output. The pin row itself is left alone so the FE
        can resurrect the favourite if access is restored.
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
            JOIN subject_pins p
              ON p.subject_id = s.id
             AND p.user_id = :uid
            WHERE s.id IN (
                SELECT subject_id FROM subject_owners    WHERE owner_user_id = :uid
                UNION
                SELECT subject_id FROM subject_shares    WHERE user_id       = :uid
                UNION
                SELECT subject_id FROM subject_assignees WHERE user_id       = :uid
            )
            ORDER BY p.pinned_at DESC, p.subject_id DESC
            LIMIT :limit
            """,
            {"uid": user_id, "limit": limit},
        ).fetchall()
        return [_row_to_summary(row) for row in rows]

    # -- private notes ---------------------------------------------------

    def upsert_note(
        self,
        subject_id: str,
        user_id: int,
        body: str,
        *,
        now: str,
    ) -> NoteRow:
        """Create or replace ``user_id``'s private note on ``subject_id``.

        On first write the ``created_at`` timestamp is set to ``now``;
        on subsequent writes only ``updated_at`` advances. We store
        both even though the wire shape only exposes ``updated_at``,
        because the difference is useful for audit logs and future
        "new vs edited" UI affordances.
        """
        existing = self._conn.execute(
            """
            SELECT created_at FROM subject_notes
            WHERE subject_id = ? AND user_id = ?
            """,
            (subject_id, user_id),
        ).fetchone()
        created_at = existing[0] if existing is not None else now
        self._conn.execute(
            """
            INSERT INTO subject_notes(
                subject_id, user_id, body, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (subject_id, user_id) DO UPDATE SET
                body = excluded.body,
                updated_at = excluded.updated_at
            """,
            (subject_id, user_id, body, created_at, now),
        )
        self._conn.commit()
        return NoteRow(
            subject_id=subject_id,
            user_id=user_id,
            body=body,
            created_at=created_at,
            updated_at=now,
        )

    def get_note(self, subject_id: str, user_id: int) -> NoteRow | None:
        """Return ``user_id``'s note on ``subject_id`` or ``None``."""
        row = self._conn.execute(
            """
            SELECT subject_id, user_id, body, created_at, updated_at
            FROM subject_notes
            WHERE subject_id = ? AND user_id = ?
            """,
            (subject_id, user_id),
        ).fetchone()
        if row is None:
            return None
        sid, uid, body, created_at, updated_at = row
        return NoteRow(
            subject_id=str(sid),
            user_id=int(uid),
            body=str(body),
            created_at=str(created_at),
            updated_at=str(updated_at),
        )

    def delete_note(self, subject_id: str, user_id: int) -> bool:
        """Drop ``user_id``'s note on ``subject_id``. Idempotent."""
        cur = self._conn.execute(
            "DELETE FROM subject_notes WHERE subject_id = ? AND user_id = ?",
            (subject_id, user_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

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

    # -- comment reactions ------------------------------------------------

    def add_reaction(
        self,
        comment_id: int,
        user_id: int,
        reaction_key: str,
        *,
        created_at: str,
    ) -> bool:
        """Insert a ``(comment, user, key)`` reaction row idempotently.

        Returns ``True`` when a new row was inserted, ``False`` if the
        same triple already existed (i.e. the actor double-clicked the
        button). The PUT endpoint surfaces both as 200 to the client —
        the bool is purely for the route layer's test seam — but
        keeping the distinction at the repository keeps the audit
        story honest if we ever start logging deltas.
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO comment_reactions(
                comment_id, user_id, reaction_key, created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (comment_id, user_id, reaction_key, created_at),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def remove_reaction(
        self,
        comment_id: int,
        user_id: int,
        reaction_key: str,
    ) -> bool:
        """Delete a single ``(comment, user, key)`` reaction row.

        Returns ``True`` if a row was actually removed, ``False`` if
        the actor never had that reaction. The route layer translates
        the latter into a 404 so a stale optimistic UI doesn't pretend
        a no-op succeeded.
        """
        cur = self._conn.execute(
            """
            DELETE FROM comment_reactions
            WHERE comment_id = ? AND user_id = ? AND reaction_key = ?
            """,
            (comment_id, user_id, reaction_key),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_reactions(self, comment_id: int) -> list[ReactionRow]:
        """Return every reaction on ``comment_id`` (oldest first).

        Ordering is ``(reaction_key ASC, created_at ASC)`` so the
        same emoji always groups together and within an emoji group
        the earliest reactor leads — good defaults for a "who
        reacted first" UI without forcing the route to re-sort.
        """
        rows = self._conn.execute(
            """
            SELECT comment_id, user_id, reaction_key, created_at
            FROM comment_reactions
            WHERE comment_id = ?
            ORDER BY reaction_key ASC, datetime(created_at) ASC, user_id ASC
            """,
            (comment_id,),
        ).fetchall()
        return [
            ReactionRow(
                comment_id=int(cid),
                user_id=int(uid),
                reaction_key=str(key),
                created_at=str(ts),
            )
            for cid, uid, key, ts in rows
        ]

    # -- labels -----------------------------------------------------------

    def add_label(
        self,
        subject_id: str,
        label: str,
        *,
        created_by: int | None,
        created_at: str,
    ) -> bool:
        """Tag ``subject_id`` with ``label``. Idempotent.

        Returns ``True`` if a new row was inserted, ``False`` if the
        label already existed on the dossier (so the route layer can
        return 200 vs 201 if it cares — but the API surface uses 200
        in both cases for ergonomic PUT semantics).
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO subject_labels(
                subject_id, label, created_by, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (subject_id, label, created_by, created_at),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def remove_label(self, subject_id: str, label: str) -> bool:
        """Detach ``label`` from ``subject_id``. Returns whether a row was removed."""
        cur = self._conn.execute(
            "DELETE FROM subject_labels WHERE subject_id = ? AND label = ?",
            (subject_id, label),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_labels(self, subject_id: str) -> list[LabelRow]:
        """All labels on a dossier, ordered alphabetically.

        Alphabetical (rather than insertion-order) is the convention
        every other tag-list UI follows — fast scan, no surprise
        re-flow when somebody re-applies an existing label.
        """
        rows = self._conn.execute(
            """
            SELECT subject_id, label, created_by, created_at
            FROM subject_labels
            WHERE subject_id = ?
            ORDER BY label ASC
            """,
            (subject_id,),
        ).fetchall()
        return [
            LabelRow(
                subject_id=str(sid),
                label=str(lab),
                created_by=None if cb is None else int(cb),
                created_at=str(ts),
            )
            for sid, lab, cb, ts in rows
        ]

    def list_label_catalog(self, user_id: int, *, is_admin: bool = False) -> list[tuple[str, int]]:
        """Distinct labels the actor can see, with counts.

        Powers the global "filter by label" UI: only counts dossiers
        the actor can read (owner / share / assignment), or all
        dossiers if ``is_admin``. Sorted by descending count then
        label, so the most-used tags surface first.
        """
        if is_admin:
            rows = self._conn.execute(
                """
                SELECT label, COUNT(*) AS n
                FROM subject_labels
                GROUP BY label
                ORDER BY n DESC, label ASC
                """,
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT l.label, COUNT(*) AS n
                FROM subject_labels l
                WHERE l.subject_id IN (
                    SELECT subject_id FROM subject_owners
                        WHERE owner_user_id = :uid
                    UNION
                    SELECT subject_id FROM subject_shares
                        WHERE user_id = :uid
                    UNION
                    SELECT subject_id FROM subject_assignees
                        WHERE user_id = :uid
                )
                GROUP BY l.label
                ORDER BY n DESC, l.label ASC
                """,
                {"uid": user_id},
            ).fetchall()
        return [(str(lab), int(n)) for lab, n in rows]

    # -- status -----------------------------------------------------------

    def get_status(self, subject_id: str) -> StatusRow | None:
        """Return the explicit status row for a subject, or ``None``.

        ``None`` here means "no row in subject_status" — the route
        layer projects this into the default ``"open"`` state. We
        deliberately don't synthesise a fake row at the repository
        level so callers can distinguish "never moved off the
        default" from "explicitly re-opened" if they care.
        """
        row = self._conn.execute(
            """
            SELECT subject_id, status, updated_by, updated_at
            FROM subject_status WHERE subject_id = ?
            """,
            (subject_id,),
        ).fetchone()
        if row is None:
            return None
        sid, st, ub, ts = row
        return StatusRow(
            subject_id=str(sid),
            status=str(st),
            updated_by=None if ub is None else int(ub),
            updated_at=str(ts),
        )

    def set_status(
        self,
        subject_id: str,
        status: str,
        *,
        updated_by: int | None,
        updated_at: str,
    ) -> StatusRow:
        """Upsert the status row for a subject. Returns the new row.

        We always write a row (even on transitions to the default
        ``"open"`` state) so the audit trail of who last touched
        the dossier survives ping-pong transitions like
        open → closed → open.
        """
        self._conn.execute(
            """
            INSERT INTO subject_status(subject_id, status, updated_by, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(subject_id) DO UPDATE SET
                status = excluded.status,
                updated_by = excluded.updated_by,
                updated_at = excluded.updated_at
            """,
            (subject_id, status, updated_by, updated_at),
        )
        self._conn.commit()
        return StatusRow(
            subject_id=subject_id,
            status=status,
            updated_by=updated_by,
            updated_at=updated_at,
        )

    def status_counts(self, user_id: int, *, is_admin: bool = False) -> dict[str, int]:
        """Return ``{status: count}`` for dossiers visible to the actor.

        Powers the sidebar's status-bucket headers (``Open (12) /
        On hold (3) / Closed (47)``). The ``"open"`` bucket includes
        every visible dossier *without* an explicit status row, so a
        brand-new dossier counts as open even before the route layer
        materialises a row for it.
        """
        if is_admin:
            visible_subjects_sql = "SELECT id AS subject_id FROM subjects"
            params: dict[str, object] = {}
        else:
            visible_subjects_sql = """
                SELECT subject_id FROM subject_owners    WHERE owner_user_id = :uid
                UNION
                SELECT subject_id FROM subject_shares    WHERE user_id       = :uid
                UNION
                SELECT subject_id FROM subject_assignees WHERE user_id       = :uid
            """
            params = {"uid": user_id}

        rows = self._conn.execute(
            f"""
            SELECT
                COALESCE(s.status, 'open') AS bucket,
                COUNT(*) AS n
            FROM ({visible_subjects_sql}) AS visible
            LEFT JOIN subject_status s ON s.subject_id = visible.subject_id
            GROUP BY bucket
            """,
            params,
        ).fetchall()
        return {str(bucket): int(n) for bucket, n in rows}

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

    def list_activity(
        self,
        subject_id: str,
        *,
        limit: int = 50,
        excerpt_chars: int = 200,
    ) -> list[ActivityRow]:
        """Chronological activity feed for ``subject_id`` (newest first).

        Aggregates four event kinds out of the existing tables — without
        adding a separate ``activity`` table — so anything that already
        gets persisted (a comment, an assignment, a share, an anchor
        mint) automatically shows up here without a second write path
        that could drift. Conversely, an event we *don't* persist (a
        delete, an ownership change) is intentionally not reflected; we
        prefer "missing but consistent" over "synthesised guess".

        Ordering
        --------

        SQLite's lexical TEXT compare matches ISO-8601 ordering, so a
        ``datetime(created_at) DESC`` sort gives us a stable feed. Within
        a single millisecond, ``tiebreak DESC`` keeps the order stable
        across calls — the comment auto-id, then the assignee/share user
        id, then ``0`` for anchors. The two non-zero tiebreakers are
        unique per event kind, so distinct events never collide.

        Parameters
        ----------
        limit:
            Cap on returned rows. Negative or zero short-circuits to an
            empty list to mirror :meth:`list_visible_summaries`.
        excerpt_chars:
            Max characters of comment body included in ``excerpt``;
            longer comments are truncated client-side as well, but we
            cap server-side too so a 10k-char comment doesn't bloat the
            feed payload by 50x.
        """
        if limit <= 0:
            return []
        rows = self._conn.execute(
            """
            SELECT kind, actor_id, target_id, excerpt, ts FROM (
                SELECT
                    'comment_added'                    AS kind,
                    author_user_id                     AS actor_id,
                    NULL                               AS target_id,
                    SUBSTR(body, 1, :excerpt_chars)    AS excerpt,
                    created_at                         AS ts,
                    id                                 AS tiebreak
                FROM subject_comments
                WHERE subject_id = :sid
                UNION ALL
                SELECT
                    'assigned',
                    assigned_by,
                    user_id,
                    NULL,
                    assigned_at,
                    user_id
                FROM subject_assignees
                WHERE subject_id = :sid
                UNION ALL
                SELECT
                    'shared',
                    NULL,
                    user_id,
                    NULL,
                    created_at,
                    user_id
                FROM subject_shares
                WHERE subject_id = :sid
                UNION ALL
                SELECT
                    'anchored',
                    NULL,
                    NULL,
                    NULL,
                    (SELECT created_at FROM subjects WHERE id = :sid),
                    0
                FROM dossier_anchors
                WHERE subject_id = :sid
            )
            ORDER BY datetime(ts) DESC, tiebreak DESC
            LIMIT :limit
            """,
            {"sid": subject_id, "excerpt_chars": excerpt_chars, "limit": limit},
        ).fetchall()
        return [
            ActivityRow(
                kind=str(kind),
                actor_user_id=None if actor_id is None else int(actor_id),
                target_user_id=None if target_id is None else int(target_id),
                excerpt=None if excerpt is None else str(excerpt),
                created_at=str(ts),
            )
            for kind, actor_id, target_id, excerpt, ts in rows
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
