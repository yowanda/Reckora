"""SQLite-backed user repository.

The users table lives in the same file as the subjects/traces/edges tables so
the API and CLI share a single backing store. Schema is created lazily on
construction so first-run on an empty file Just Works.

Role column migration
---------------------

The original schema had no ``role`` column. When upgrading an existing
database we add the column with a one-time DEFAULT of ``'admin'`` so every
pre-existing user keeps the implicit operator privileges they had before
RBAC landed. Fresh databases get a column DEFAULT of ``'viewer'`` so
self-service registration stays scoped — the ``Role`` enum is the source
of truth on the Python side and INSERTs always set the value explicitly,
so the column-level default only matters at migration time.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from .models import Role, UserRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    role TEXT NOT NULL DEFAULT 'viewer'
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
"""


class UserRepository:
    """File-backed (or ``:memory:``) user store for the API."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._migrate_role_column()
        self._conn.commit()

    def _migrate_role_column(self) -> None:
        """Add the ``role`` column to legacy users tables.

        Pre-RBAC databases predate the column entirely. Detecting it via
        ``PRAGMA table_info`` (rather than swallowing an OperationalError on
        ALTER) keeps the migration path explicit and lets us pick the right
        DEFAULT for legacy rows: existing users were operators and stay
        ``admin`` so the upgrade does not silently revoke their access.
        """
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(users)").fetchall()}
        if "role" not in cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> UserRepository:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        role: Role = Role.VIEWER,
    ) -> UserRecord:
        ts = datetime.now(UTC).isoformat()
        cur = self._conn.execute(
            """
            INSERT INTO users (username, password_hash, created_at, is_active, role)
            VALUES (?, ?, ?, 1, ?)
            """,
            (username, password_hash, ts, role.value),
        )
        self._conn.commit()
        user_id = cur.lastrowid
        if user_id is None:  # pragma: no cover - sqlite always supplies it
            raise RuntimeError("sqlite did not return a row id")
        return UserRecord(
            id=user_id,
            username=username,
            password_hash=password_hash,
            created_at=datetime.fromisoformat(ts),
            is_active=True,
            role=role,
        )

    def set_role(self, user_id: int, role: Role) -> UserRecord | None:
        """Update the role of an existing user.

        Returns the refreshed record, or ``None`` if no row matched
        ``user_id`` so the caller can surface a 404 without a second query.
        """
        cur = self._conn.execute(
            "UPDATE users SET role = ? WHERE id = ?",
            (role.value, user_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            return None
        return self.get_by_id(user_id)

    def list_users(self) -> list[UserRecord]:
        """Return every user, oldest first.

        Used by the admin-only ``GET /api/v1/users`` endpoint. The list stays
        small in practice (operators + investigators), so an unbounded
        ``ORDER BY id`` scan is fine and keeps the call ergonomic for the
        frontend.
        """
        rows = self._conn.execute(
            """
            SELECT id, username, password_hash, created_at, is_active, role
            FROM users ORDER BY id ASC
            """
        ).fetchall()
        return [r for r in (_row_to_record(row) for row in rows) if r is not None]

    def get_by_username(self, username: str) -> UserRecord | None:
        row = self._conn.execute(
            """
            SELECT id, username, password_hash, created_at, is_active, role
            FROM users WHERE username = ?
            """,
            (username,),
        ).fetchone()
        return _row_to_record(row)

    def get_by_id(self, user_id: int) -> UserRecord | None:
        row = self._conn.execute(
            """
            SELECT id, username, password_hash, created_at, is_active, role
            FROM users WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
        return _row_to_record(row)


def _row_to_record(row: tuple[int, str, str, str, int, str] | None) -> UserRecord | None:
    if row is None:
        return None
    return UserRecord(
        id=row[0],
        username=row[1],
        password_hash=row[2],
        created_at=datetime.fromisoformat(row[3]),
        is_active=bool(row[4]),
        role=Role(row[5]),
    )
