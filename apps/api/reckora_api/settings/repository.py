"""SQLite-backed per-user settings store.

Lives next to :class:`UserRepository` (auth/repository.py) and shares
the same SQLite file. A single row per user holds the encrypted BYOK
secrets; the encryption layer is owned by :mod:`crypto` so this
module only ever touches ciphertext.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from .crypto import Encryptor

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_settings(
    user_id INTEGER PRIMARY KEY,
    agentrouter_api_key_ciphertext TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""


@dataclass(frozen=True)
class UserSettingsRecord:
    """Internal settings row; never returned to clients verbatim.

    ``agentrouter_api_key_ciphertext`` is the at-rest Fernet token —
    callers that need the plaintext key go through
    :meth:`UserSettingsRepository.get_agentrouter_key`.
    """

    user_id: int
    agentrouter_api_key_ciphertext: str | None
    created_at: datetime
    updated_at: datetime


class UserSettingsRepository:
    """File-backed (or ``:memory:``) per-user settings store."""

    def __init__(self, path: str | Path, encryptor: Encryptor) -> None:
        self.path = str(path)
        self._encryptor = encryptor
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> UserSettingsRepository:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def get(self, user_id: int) -> UserSettingsRecord | None:
        row = self._conn.execute(
            """
            SELECT user_id, agentrouter_api_key_ciphertext, created_at, updated_at
            FROM user_settings WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def get_agentrouter_key(self, user_id: int) -> str | None:
        """Return the user's plaintext AgentRouter API key, or ``None``.

        The decrypt happens inside the repository so the route layer
        never sees ciphertext directly. Callers must treat the
        returned string as a secret and never log it.
        """
        record = self.get(user_id)
        if record is None or record.agentrouter_api_key_ciphertext is None:
            return None
        return self._encryptor.decrypt(record.agentrouter_api_key_ciphertext)

    def set_agentrouter_key(self, user_id: int, plaintext: str) -> UserSettingsRecord:
        """Save / replace the user's AgentRouter API key.

        Empty / whitespace-only inputs are rejected upstream by the
        Pydantic schema, but as a defensive measure we strip and
        reject them here as well so a buggy caller cannot persist a
        sentinel that ``has_agentrouter_key`` would mis-classify.
        """
        cleaned = plaintext.strip()
        if not cleaned:
            raise ValueError("plaintext key must be non-empty")
        ciphertext = self._encryptor.encrypt(cleaned)
        return self._upsert(user_id, ciphertext)

    def clear_agentrouter_key(self, user_id: int) -> UserSettingsRecord:
        """Drop the user's AgentRouter API key, leaving the row in place."""
        return self._upsert(user_id, None)

    def _upsert(
        self,
        user_id: int,
        ciphertext: str | None,
    ) -> UserSettingsRecord:
        ts = datetime.now(UTC).isoformat()
        self._conn.execute(
            """
            INSERT INTO user_settings (user_id, agentrouter_api_key_ciphertext,
                                       created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                agentrouter_api_key_ciphertext = excluded.agentrouter_api_key_ciphertext,
                updated_at = excluded.updated_at
            """,
            (user_id, ciphertext, ts, ts),
        )
        self._conn.commit()
        record = self.get(user_id)
        if record is None:  # pragma: no cover - upsert always lands a row
            raise RuntimeError("sqlite upsert did not produce a row")
        return record


def _row_to_record(
    row: tuple[int, str | None, str, str],
) -> UserSettingsRecord:
    user_id, ciphertext, created_at, updated_at = row
    return UserSettingsRecord(
        user_id=user_id,
        agentrouter_api_key_ciphertext=ciphertext,
        created_at=datetime.fromisoformat(created_at),
        updated_at=datetime.fromisoformat(updated_at),
    )
