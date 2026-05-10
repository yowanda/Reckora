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
    HACKERNEWS_API = "hackernews_api"
    KEYBASE_API = "keybase_api"
    GRAVATAR_API = "gravatar_api"
    WHOIS_RDAP = "whois_rdap"
    DNS_RESOLVER = "dns_resolver"
    WEB_PROFILE = "web_profile"
    PHONE_LIBPHONENUMBER = "phone_libphonenumber"
    BREACH_HIBP = "breach_hibp"
    WALLET_BLOCKSTREAM = "wallet_blockstream"
    WALLET_ETHERSCAN = "wallet_etherscan"
    WALLET_SOLANA = "wallet_solana"
    AVATAR_HTTP = "avatar_http"
    EMAIL_PROFILE = "email_profile"
    REDDIT_PROFILE = "reddit_profile"
    X_SYNDICATION = "x_syndication"
    TIKTOK_WEB = "tiktok_web"
    SOCIAL_PRESENCE_PROBE = "social_presence_probe"
    DOC_LEAK = "doc_leak"
    USER_PROVIDED = "user_provided"
    WEB_RESEARCH = "web_research"


class EdgeKind(StrEnum):
    """The semantic relationship a correlation Edge represents."""

    SAME_AVATAR = "same_avatar"
    SIMILAR_BIO = "similar_bio"
    USERNAME_MUTATION = "username_mutation"
    TIMEZONE_OVERLAP = "timezone_overlap"
    SHARED_REGISTRANT = "shared_registrant"
