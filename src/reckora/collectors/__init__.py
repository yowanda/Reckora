"""Collectors — pluggable adapters that turn an Identifier into Traces.

Each collector implements `Collector.collect(identifier) -> list[Trace]`.
Collectors must be stateless and side-effect-free aside from outbound HTTP.
"""

from __future__ import annotations

from .avatar import AvatarCollector
from .base import Collector
from .dns_records import DNSCollector
from .github_api import GitHubCollector
from .gravatar import GravatarCollector
from .hackernews import HackerNewsCollector
from .keybase import KeybaseCollector
from .phone import PhoneCollector
from .social_presence import SocialPresenceProbeCollector
from .tiktok import TikTokCollector
from .wallet_btc import BitcoinChainCollector
from .wallet_eth import EthereumChainCollector
from .wallet_sol import SolanaChainCollector
from .web_profile import WebProfileCollector
from .whois_rdap import WhoisRdapCollector
from .x_twitter import XCollector

__all__ = [
    "AvatarCollector",
    "BitcoinChainCollector",
    "Collector",
    "DNSCollector",
    "EthereumChainCollector",
    "GitHubCollector",
    "GravatarCollector",
    "HackerNewsCollector",
    "KeybaseCollector",
    "PhoneCollector",
    "SocialPresenceProbeCollector",
    "SolanaChainCollector",
    "TikTokCollector",
    "WebProfileCollector",
    "WhoisRdapCollector",
    "XCollector",
]
