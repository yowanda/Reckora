"""Symmetric encryption for at-rest user secrets.

The encryptor wraps :class:`cryptography.fernet.Fernet` (AES-128-CBC +
HMAC-SHA-256) with a small bootstrap layer so a vanilla single-host
deployment never has to think about key management:

* If ``RECKORA_API_FERNET_KEY_PATH`` is set, the key is read from
  that file. The file must hold a single line of urlsafe-base64
  bytes of length 44 (the standard Fernet key shape).
* Otherwise, we co-locate a key file next to the SQLite database
  (``${RECKORA_DB_PATH}.fernet``). When the file is missing we
  generate a fresh key with :meth:`Fernet.generate_key` and persist
  it with mode ``0600`` so future restarts find the same key.

Operators must back this file up alongside the database. Losing it
makes every previously-saved BYOK key unrecoverable, which is the
desired property if the host is compromised: the database alone
leaks no plaintext.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class Encryptor:
    """Encrypt / decrypt short secrets with a deployment-scoped Fernet key."""

    def __init__(self, key: bytes) -> None:
        # Validate eagerly so a malformed key surfaces at startup
        # rather than on the first encryption call.
        self._fernet = Fernet(key)

    @classmethod
    def from_path(cls, key_path: str | Path) -> Encryptor:
        """Load the Fernet key from disk, generating one if absent.

        The on-disk format is the same urlsafe-base64 string returned
        by :meth:`Fernet.generate_key`. The parent directory is
        created on the fly so callers can hand in a path under the
        same directory as the SQLite file without an extra ``mkdir``
        step.
        """
        path = Path(key_path)
        if path.exists():
            return cls(_read_key(path))
        # Auto-bootstrap: generate, persist with 0600, return.
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        path.write_bytes(key)
        with contextlib.suppress(OSError):
            # Windows or other filesystems that don't honour POSIX
            # permissions — best effort. The file is still readable
            # only by the user that started the process by default.
            path.chmod(0o600)
        return cls(key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a UTF-8 string and return the urlsafe-base64 token.

        Stored verbatim in the database (TEXT column) — the column
        round-trips through ``str`` so no extra encoding step is
        needed at the SQL boundary.
        """
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        """Decrypt a Fernet token previously produced by :meth:`encrypt`.

        Raises :class:`cryptography.fernet.InvalidToken` if the token
        was forged, truncated, or encrypted with a different key.
        Callers should treat that as a fatal configuration error and
        not try to recover (mismatched key + ciphertext is unsafe to
        paper over).
        """
        return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")


def _read_key(path: Path) -> bytes:
    """Read the on-disk Fernet key, stripping incidental whitespace.

    Editors that "helpfully" append a trailing newline would otherwise
    push the key past the 44-byte length expected by Fernet.
    """
    raw = path.read_bytes().strip()
    if len(raw) == 0:
        raise InvalidToken(f"fernet key file at {path} is empty")
    return raw


__all__ = ["Encryptor", "InvalidToken"]
