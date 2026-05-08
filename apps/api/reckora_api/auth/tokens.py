"""JWT token issuance and verification.

We use HS256 with a single shared secret. The token carries the user id in
``sub``, plus standard ``iat`` / ``exp`` claims. Anything else the API needs
is loaded from the database on each request via :func:`current_user`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt


def create_token(
    *,
    subject: str,
    secret: str,
    ttl_seconds: int = 3600,
    algorithm: str = "HS256",
) -> str:
    """Issue a fresh access token. ``subject`` is typically the user id as a string."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_token(
    token: str,
    *,
    secret: str,
    algorithm: str = "HS256",
) -> dict[str, Any]:
    """Verify and decode a token. Raises :class:`jwt.PyJWTError` on any failure."""
    return jwt.decode(token, secret, algorithms=[algorithm])
