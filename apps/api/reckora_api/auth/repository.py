"""SQLite-backed user repository.

The users table lives in the same file as the subjects/traces/edges tables so
the API and CLI share a single backing store. Schema is created lazily on
construction so first-run on an empty file Just Works.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from .models import UserRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
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
        self._conn.commit()

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

    def create_user(self, *, username: str, password_hash: str) -> UserRecord:
        ts = datetime.now(UTC).isoformat()
        cur = self._conn.execute(
            """
            INSERT INTO users (username, password_hash, created_at, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (username, password_hash, ts),
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
        )

    def get_by_username(self, username: str) -> UserRecord | None:
        row = self._conn.execute(
            """
            SELECT id, username, password_hash, created_at, is_active
            FROM users WHERE username = ?
            """,
            (username,),
        ).fetchone()
        return _row_to_record(row)

    def get_by_id(self, user_id: int) -> UserRecord | None:
        row = self._conn.execute(
            """
            SELECT id, username, password_hash, created_at, is_active
            FROM users WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
        return _row_to_record(row)


def _row_to_record(row: tuple[int, str, str, str, int] | None) -> UserRecord | None:
    if row is None:
        return None
    return UserRecord(
        id=row[0],
        username=row[1],
        password_hash=row[2],
        created_at=datetime.fromisoformat(row[3]),
        is_active=bool(row[4]),
    )
