"""Authentication module for the Reckora API.

Login model is username + password (`Login user` per ROADMAP). Passwords are
hashed with bcrypt; tokens are short-lived JWTs (HS256) carrying the user id.
"""

from __future__ import annotations

from .models import TokenResponse, UserCreate, UserPublic, UserRecord
from .passwords import hash_password, verify_password
from .repository import UserRepository
from .tokens import create_token, decode_token

__all__ = [
    "TokenResponse",
    "UserCreate",
    "UserPublic",
    "UserRecord",
    "UserRepository",
    "create_token",
    "decode_token",
    "hash_password",
    "verify_password",
]
