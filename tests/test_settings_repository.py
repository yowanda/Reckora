"""Unit tests for the per-user settings SQLite repository.

The repository owns the encrypt/decrypt boundary so the route layer
never sees ciphertext directly. These tests verify the boundary holds:
plaintext input is encrypted before the SQL INSERT lands, the column
in the database holds Fernet ciphertext (not the plaintext key), and
``get_agentrouter_key`` is the only path that returns plaintext.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from reckora_api.settings.crypto import Encryptor
from reckora_api.settings.repository import UserSettingsRepository


def _new_repo(tmp_path: Path) -> UserSettingsRepository:
    encryptor = Encryptor(Fernet.generate_key())
    return UserSettingsRepository(tmp_path / "settings.db", encryptor)


def test_get_returns_none_for_unknown_user(tmp_path: Path) -> None:
    repo = _new_repo(tmp_path)
    assert repo.get(user_id=42) is None
    assert repo.get_agentrouter_key(user_id=42) is None


def test_set_and_get_agentrouter_key_round_trip(tmp_path: Path) -> None:
    repo = _new_repo(tmp_path)
    repo.set_agentrouter_key(user_id=1, plaintext="sk-secret-abc")
    assert repo.get_agentrouter_key(user_id=1) == "sk-secret-abc"


def test_set_persists_ciphertext_not_plaintext(tmp_path: Path) -> None:
    """Defence in depth: the column must never hold the raw key."""
    repo = _new_repo(tmp_path)
    repo.set_agentrouter_key(user_id=1, plaintext="sk-secret-abc")
    raw = (
        sqlite3.connect(str(tmp_path / "settings.db"))
        .execute("SELECT agentrouter_api_key_ciphertext FROM user_settings WHERE user_id=1")
        .fetchone()[0]
    )
    assert raw is not None
    assert "sk-secret-abc" not in raw  # the plaintext leaks nowhere


def test_clear_removes_key_but_keeps_row(tmp_path: Path) -> None:
    repo = _new_repo(tmp_path)
    repo.set_agentrouter_key(user_id=1, plaintext="sk-secret-abc")
    repo.clear_agentrouter_key(user_id=1)
    assert repo.get_agentrouter_key(user_id=1) is None
    record = repo.get(user_id=1)
    assert record is not None  # row stays around
    assert record.agentrouter_api_key_ciphertext is None


def test_set_replaces_existing_key(tmp_path: Path) -> None:
    repo = _new_repo(tmp_path)
    repo.set_agentrouter_key(user_id=1, plaintext="first")
    repo.set_agentrouter_key(user_id=1, plaintext="second")
    assert repo.get_agentrouter_key(user_id=1) == "second"


def test_set_rejects_blank_plaintext(tmp_path: Path) -> None:
    repo = _new_repo(tmp_path)
    with pytest.raises(ValueError):
        repo.set_agentrouter_key(user_id=1, plaintext="   ")


def test_users_are_isolated(tmp_path: Path) -> None:
    repo = _new_repo(tmp_path)
    repo.set_agentrouter_key(user_id=1, plaintext="alice")
    repo.set_agentrouter_key(user_id=2, plaintext="bob")
    assert repo.get_agentrouter_key(user_id=1) == "alice"
    assert repo.get_agentrouter_key(user_id=2) == "bob"
