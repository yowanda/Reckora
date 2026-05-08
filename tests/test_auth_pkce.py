"""Unit tests for ``reckora.auth.pkce`` (RFC 7636)."""

from __future__ import annotations

import base64
import hashlib

import pytest

from reckora.auth.pkce import generate_code_challenge, generate_code_verifier


def test_default_verifier_length_is_in_spec_window() -> None:
    """Default verifier should sit comfortably inside the 43..128 window."""
    verifier = generate_code_verifier()
    assert 43 <= len(verifier) <= 128


def test_explicit_verifier_length_round_trips() -> None:
    for length in (43, 64, 96, 128):
        assert len(generate_code_verifier(length)) == length


@pytest.mark.parametrize("length", [0, 1, 42, 129, 1000])
def test_verifier_rejects_out_of_spec_lengths(length: int) -> None:
    with pytest.raises(ValueError, match="code_verifier length"):
        generate_code_verifier(length)


def test_verifier_uses_url_safe_alphabet() -> None:
    """Verifiers must only contain RFC 7636 unreserved characters."""
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    for _ in range(20):
        verifier = generate_code_verifier()
        assert set(verifier).issubset(allowed)


def test_two_verifiers_are_distinct() -> None:
    """``secrets`` should give us cryptographically-distinct values."""
    seen: set[str] = set()
    for _ in range(50):
        seen.add(generate_code_verifier())
    assert len(seen) == 50


def test_challenge_is_s256_base64url_no_pad() -> None:
    """Challenge equals ``base64url-no-pad(SHA-256(verifier))`` per spec."""
    verifier = "x" * 64
    expected_digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(expected_digest).rstrip(b"=").decode("ascii")
    assert generate_code_challenge(verifier) == expected
    # Sanity: 32-byte digest base64url-encoded → 43 chars without padding.
    assert len(expected) == 43


def test_challenge_is_deterministic() -> None:
    verifier = generate_code_verifier()
    assert generate_code_challenge(verifier) == generate_code_challenge(verifier)


def test_challenge_omits_padding() -> None:
    """OpenAI rejects challenges that include the trailing ``=`` pad."""
    challenge = generate_code_challenge("v" * 50)
    assert "=" not in challenge


def test_challenge_rejects_non_ascii_verifier() -> None:
    """Surface non-conforming verifiers loudly rather than silently
    encoding them as UTF-8 (which would produce a challenge OpenAI
    will not accept)."""
    with pytest.raises(UnicodeEncodeError):
        generate_code_challenge("verifier-with-non-ascii-é")
