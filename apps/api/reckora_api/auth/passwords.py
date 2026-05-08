"""Password hashing helpers (bcrypt + sha256 pre-digest).

bcrypt limits inputs to 72 bytes. We pre-hash the user's password with
sha256-hex so passwords longer than 72 bytes still produce a deterministic
fixed-length input. The output is the raw bcrypt hash, encoded as a 60-byte
ASCII string suitable for SQLite storage.
"""

from __future__ import annotations

import hashlib

import bcrypt


def _prepare(plain: str) -> bytes:
    """Reduce arbitrary-length user input to a stable 64-byte ASCII digest."""
    return hashlib.sha256(plain.encode("utf-8")).hexdigest().encode("ascii")


def hash_password(plain: str) -> str:
    """Hash a plaintext password. Cost is 12 rounds of bcrypt (~250ms / hash)."""
    return bcrypt.hashpw(_prepare(plain), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time check; returns ``False`` on any malformed hash."""
    try:
        return bcrypt.checkpw(_prepare(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False
