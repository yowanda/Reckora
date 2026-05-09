"""Identifier-kind auto-detection.

Pure heuristics over the string form of an identifier, so the CLI
``reckora investigate alice@example.com`` does the right thing without
``--kind email`` boilerplate. The same helpers are exported as a library
so higher layers (API, agent loop) can normalize a user-supplied hint
into a typed :class:`Identifier`.

The rules are deliberately narrow: when a string genuinely looks like
more than one kind (``http://...x.png`` could be both ``url`` and
``avatar``), we pick the more specific match. When nothing matches we
return ``None`` rather than guessing, so the caller can ask the user.

A few intentionally-accepted ambiguities:

* Bare ``user.name`` resolves to ``DOMAIN``. Most OSINT pipelines never
  pass a bare dotted username — the dot is overwhelmingly a hostname
  separator. Operators who really mean ``USERNAME`` can pass
  ``--kind username``.
* Solana addresses share their alphabet with bech32 BTC bodies. We
  check BTC patterns first so a ``bc1...`` string lands on ``WALLET``
  via the BTC branch rather than falsely tripping the Solana rule.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .entity import Identifier
from .enums import IdentifierType

# RFC 5322-lite. Tight enough to reject ``alice@`` but loose enough to
# accept the punctuation real OSINT inputs carry (``+`` aliases, ``.``,
# IDN-free hostnames).
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# E.164 with permissive separators. Demands a leading ``+`` so we never
# misclassify a numeric username (``12345``) as a phone number.
_PHONE_RE = re.compile(r"^\+\d[\d\s\-().]{6,30}$")

# ETH/EVM externally owned account.
_ETH_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

# BTC: legacy P2PKH (``1...``), P2SH (``3...``), bech32 (``bc1...``).
_BTC_RE = re.compile(r"^(?:[13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[a-z0-9]{25,62})$")

# Solana base58 — alphabet excludes ``0``, ``O``, ``I``, ``l``. Real
# addresses are 32-44 chars; we use the full range so vanity addresses
# don't fall off either end.
_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# FQDN: at least one dot-separated label and a 2-63 char alpha TLD.
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)"
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z]{2,63}$"
)

# Username: 1-64 chars in the typical handle alphabet.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")

_IMAGE_SUFFIXES: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
)


def detect_identifier_kind(value: str) -> IdentifierType | None:
    """Best-effort auto-detection of the :class:`IdentifierType` for ``value``.

    Returns ``None`` if no heuristic fires; callers should treat that as
    a request to ask the user instead of silently falling back.
    """
    s = value.strip()
    if not s:
        return None

    # URL / avatar — must precede email since URLs may contain ``@``.
    if s.startswith(("http://", "https://")):
        path_lower = urlparse(s).path.lower()
        if any(path_lower.endswith(suffix) for suffix in _IMAGE_SUFFIXES):
            return IdentifierType.AVATAR
        return IdentifierType.URL

    # Email.
    if "@" in s and _EMAIL_RE.match(s):
        return IdentifierType.EMAIL

    # Phone — E.164-ish.
    if s.startswith("+") and _PHONE_RE.match(s):
        return IdentifierType.PHONE

    # Wallets — order matters (see module docstring).
    if _ETH_RE.match(s):
        return IdentifierType.WALLET
    if _BTC_RE.match(s):
        return IdentifierType.WALLET
    if _SOL_RE.match(s):
        return IdentifierType.WALLET

    # Domain — must contain a dot and look like an FQDN.
    if "." in s and _DOMAIN_RE.match(s):
        return IdentifierType.DOMAIN

    # Username fallback.
    if _USERNAME_RE.match(s):
        return IdentifierType.USERNAME

    return None


def parse_identifier(
    value: str,
    kind: IdentifierType | None = None,
) -> Identifier:
    """Build an :class:`Identifier` from a raw string.

    When ``kind`` is provided we use it directly. Otherwise we
    auto-detect; a string that no rule fires on raises
    :class:`ValueError` so the caller can prompt the user for ``--kind``.
    """
    s = value.strip()
    if kind is None:
        detected = detect_identifier_kind(s)
        if detected is None:
            raise ValueError(
                f"could not auto-detect identifier kind for {value!r}; pass --kind explicitly"
            )
        kind = detected
    return Identifier(type=kind, value=s)
