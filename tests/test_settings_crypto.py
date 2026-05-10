"""Unit tests for the settings encryption layer.

The :class:`Encryptor` wraps :class:`cryptography.fernet.Fernet` with
an on-disk bootstrap helper. These tests exercise both the round-trip
contract (encrypt -> decrypt is the identity on UTF-8 strings) and
the bootstrap behaviours (auto-generate, persist, refuse mismatched
keys) so a regression in either half breaks loudly.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

from reckora_api.settings.crypto import Encryptor


def test_round_trip_recovers_plaintext(tmp_path: Path) -> None:
    enc = Encryptor.from_path(tmp_path / "k.fernet")
    plaintext = "sk-or-v1-very-secret-token"
    token = enc.encrypt(plaintext)
    assert token != plaintext  # ciphertext must not echo plaintext
    assert enc.decrypt(token) == plaintext


def test_unicode_round_trip(tmp_path: Path) -> None:
    """Non-ASCII secrets must survive the UTF-8 boundary intact."""
    enc = Encryptor.from_path(tmp_path / "k.fernet")
    plaintext = "héllo-örld-Ω-公益站"
    assert enc.decrypt(enc.encrypt(plaintext)) == plaintext


def test_auto_generates_key_on_first_call(tmp_path: Path) -> None:
    """Calling ``from_path`` against a missing file must materialise it."""
    key_path = tmp_path / "subdir" / "k.fernet"
    assert not key_path.exists()
    Encryptor.from_path(key_path)
    assert key_path.exists()
    # Persisted key must be a valid Fernet key (round-trippable).
    Fernet(key_path.read_bytes())


def test_auto_generated_key_persists_across_instances(tmp_path: Path) -> None:
    """A second ``from_path`` call must read the same key."""
    key_path = tmp_path / "k.fernet"
    a = Encryptor.from_path(key_path)
    token = a.encrypt("payload")
    b = Encryptor.from_path(key_path)
    assert b.decrypt(token) == "payload"


def test_auto_generated_key_has_strict_permissions(tmp_path: Path) -> None:
    """Bootstrap must apply 0600 (best-effort) on POSIX filesystems."""
    if os.name != "posix":
        pytest.skip("POSIX-only permissions check")
    key_path = tmp_path / "k.fernet"
    Encryptor.from_path(key_path)
    mode = stat.S_IMODE(key_path.stat().st_mode)
    assert mode == 0o600


def test_decrypt_rejects_token_from_different_key(tmp_path: Path) -> None:
    """Mismatched key + ciphertext must raise (never paper over)."""
    a = Encryptor.from_path(tmp_path / "a.fernet")
    b = Encryptor.from_path(tmp_path / "b.fernet")
    token = a.encrypt("only-a-knows")
    with pytest.raises(InvalidToken):
        b.decrypt(token)


def test_decrypt_rejects_garbled_token(tmp_path: Path) -> None:
    """Truncated / forged tokens raise InvalidToken."""
    enc = Encryptor.from_path(tmp_path / "k.fernet")
    token = enc.encrypt("payload")
    with pytest.raises(InvalidToken):
        enc.decrypt(token[:-4])


def test_explicit_invalid_key_raises_eagerly() -> None:
    """An obviously-malformed key must be rejected at construction.

    Fernet wraps the underlying error in a ``ValueError`` (length
    mismatch) or ``binascii.Error`` (bad base64). Any of those
    surfaces is acceptable — the contract is "fail fast", not a
    specific exception class.
    """
    with pytest.raises((ValueError, TypeError)):
        Encryptor(b"not-a-real-fernet-key")
