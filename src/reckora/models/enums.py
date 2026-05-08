"""Enumerations for the Reckora entity model.

Kept in their own module so we can extend them (new identifier types,
new collector sources, new edge kinds) without touching `entity.py`.
"""

from __future__ import annotations

from enum import StrEnum


class IdentifierType(StrEnum):
    """The kind of public identifier we are reasoning about."""

    USERNAME = "username"
    EMAIL = "email"
    DOMAIN = "domain"
    URL = "url"
    PHONE = "phone"
    WALLET = "wallet"
    AVATAR = "avatar"


class TraceSource(StrEnum):
    """Where a Trace was collected from."""

    GITHUB_API = "github_api"
    WHOIS_RDAP = "whois_rdap"
    WEB_PROFILE = "web_profile"
    PHONE_LIBPHONENUMBER = "phone_libphonenumber"
    BREACH_HIBP = "breach_hibp"
    WALLET_BLOCKSTREAM = "wallet_blockstream"
    USER_PROVIDED = "user_provided"


class EdgeKind(StrEnum):
    """The semantic relationship a correlation Edge represents."""

    SAME_AVATAR = "same_avatar"
    SIMILAR_BIO = "similar_bio"
    USERNAME_MUTATION = "username_mutation"
    TIMEZONE_OVERLAP = "timezone_overlap"
    SHARED_REGISTRANT = "shared_registrant"
