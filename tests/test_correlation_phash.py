"""Tests for the avatar-pHash rule."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from reckora.correlation.rules.avatar_phash import (
    hamming,
    hash_image_bytes,
    score,
)


def test_hamming_distance_zero_for_same_hash() -> None:
    h = "0123456789abcdef"
    assert hamming(h, h) == 0


def test_hamming_distance_known() -> None:
    # 0xff vs 0x00 differs in 8 bits per nibble pair
    assert hamming("ff", "00") == 8


def test_hamming_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        hamming("abc", "abcd")


def test_score_exact_match_high_weight() -> None:
    h = "ffeeddccbbaa9988"
    contrib = score(h, h)
    assert contrib is not None
    assert contrib.weight >= 0.94


def test_score_close_match_below_max() -> None:
    contrib = score("ffeeddccbbaa9988", "ffeeddccbbaa9989", max_distance=5)
    assert contrib is not None
    assert 0.5 < contrib.weight < 0.95


def test_score_above_max_distance_returns_none() -> None:
    # ffff vs 0000 differs in 16 bits, which is > 5
    assert score("ffff", "0000", max_distance=5) is None


def test_score_invalid_hash_returns_none() -> None:
    assert score("abc", "abcd") is None


def test_hash_image_bytes_returns_hex_string() -> None:
    img = Image.new("RGB", (32, 32), color=(123, 222, 64))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    h = hash_image_bytes(buf.getvalue())
    assert isinstance(h, str)
    assert len(h) == 16  # 8x8 dHash -> 64 bits -> 16 hex chars
