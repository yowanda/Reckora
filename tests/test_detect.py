"""Tests for :mod:`reckora.models.detect`."""

from __future__ import annotations

import pytest

from reckora.models.detect import detect_identifier_kind, parse_identifier
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Username — bare handles in the typical alphabet.
        ("octocat", IdentifierType.USERNAME),
        ("a.b-c_d", IdentifierType.USERNAME),
        ("Octocat42", IdentifierType.USERNAME),
        # Email.
        ("alice@example.com", IdentifierType.EMAIL),
        ("a+b@x.co.id", IdentifierType.EMAIL),
        ("user.name+tag@sub.domain.example", IdentifierType.EMAIL),
        # URL.
        ("https://example.com/about", IdentifierType.URL),
        ("http://localhost/", IdentifierType.URL),
        ("https://github.com/octocat", IdentifierType.URL),
        # Avatar — image-suffix URLs.
        ("https://gravatar.com/avatar/x.png", IdentifierType.AVATAR),
        ("https://example.com/me.JPG", IdentifierType.AVATAR),
        ("http://cdn.example.com/p/avatar.webp", IdentifierType.AVATAR),
        # Phone.
        ("+628123456789", IdentifierType.PHONE),
        ("+1 (415) 555-0100", IdentifierType.PHONE),
        ("+44-20-7946-0958", IdentifierType.PHONE),
        # Wallet — ETH.
        ("0x742d35Cc6634C0532925a3b844Bc9e7595f0bEAd", IdentifierType.WALLET),
        # Wallet — BTC P2PKH.
        ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", IdentifierType.WALLET),
        # Wallet — BTC bech32.
        ("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq", IdentifierType.WALLET),
        # Wallet — Solana.
        ("DjVE6JNiYqPL2QXyCUUh8rNjHrbz9hXHNYt99MQ59qw1", IdentifierType.WALLET),
        # Domain.
        ("example.com", IdentifierType.DOMAIN),
        ("sub.domain.co.id", IdentifierType.DOMAIN),
        ("yowanda.dev", IdentifierType.DOMAIN),
    ],
)
def test_detect_identifier_kind_recognises_all_kinds(value: str, expected: IdentifierType) -> None:
    assert detect_identifier_kind(value) is expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "alice@",  # malformed email — also not a valid username (has @)
        "@@@",
        "+",  # empty phone after +
        " " * 5,
    ],
)
def test_detect_returns_none_for_unparseable(value: str) -> None:
    assert detect_identifier_kind(value) is None


def test_detect_falls_back_to_username_for_handle_shaped_strings() -> None:
    """Strings that look like a wallet but fail the strict pattern (e.g.
    ``0xnothex`` — invalid hex) fall through to the USERNAME default.
    Documenting the behaviour so a future change is intentional."""
    assert detect_identifier_kind("0xnothex") is IdentifierType.USERNAME


def test_detect_strips_whitespace() -> None:
    assert detect_identifier_kind("  octocat  ") is IdentifierType.USERNAME
    assert detect_identifier_kind("\talice@example.com\n") is IdentifierType.EMAIL


def test_detect_prefers_avatar_over_url_for_image_suffix() -> None:
    """An image-suffixed URL is more specific than a generic URL."""
    assert detect_identifier_kind("https://example.com/avatar.png") is IdentifierType.AVATAR
    assert detect_identifier_kind("https://example.com/about") is IdentifierType.URL


def test_detect_treats_bare_dotted_handle_as_domain() -> None:
    """Documented behaviour: ``user.name`` resolves to DOMAIN.

    Operators who mean USERNAME can override with ``--kind username``.
    The point of the test is to pin the behaviour so a future change is
    deliberate.
    """
    assert detect_identifier_kind("user.name") is IdentifierType.DOMAIN


def test_detect_url_over_email_when_url_contains_at_sign() -> None:
    """A URL must take precedence over the ``@`` rule for email."""
    assert detect_identifier_kind("https://example.com/u/foo@bar") is IdentifierType.URL


def test_parse_identifier_with_explicit_kind() -> None:
    ident = parse_identifier("octocat", kind=IdentifierType.USERNAME)
    assert ident == Identifier(type=IdentifierType.USERNAME, value="octocat")


def test_parse_identifier_auto_detects_when_kind_omitted() -> None:
    ident = parse_identifier("alice@example.com")
    assert ident.type is IdentifierType.EMAIL
    assert ident.value == "alice@example.com"


def test_parse_identifier_strips_whitespace_in_value() -> None:
    ident = parse_identifier("  octocat  ")
    assert ident == Identifier(type=IdentifierType.USERNAME, value="octocat")


def test_parse_identifier_raises_on_undetectable() -> None:
    with pytest.raises(ValueError, match="could not auto-detect"):
        parse_identifier("alice@")


def test_parse_identifier_explicit_kind_skips_detection() -> None:
    """Explicit ``kind`` must win even when the string would otherwise
    auto-detect to a different type."""
    ident = parse_identifier("example.com", kind=IdentifierType.USERNAME)
    assert ident.type is IdentifierType.USERNAME
    assert ident.value == "example.com"
