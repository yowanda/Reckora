"""Collectors — pluggable adapters that turn an Identifier into Traces.

Each collector implements `Collector.collect(identifier) -> list[Trace]`.
Collectors must be stateless and side-effect-free aside from outbound HTTP.
"""

from __future__ import annotations

from .base import Collector
from .github_api import GitHubCollector
from .phone import PhoneCollector
from .wallet_btc import BitcoinChainCollector
from .wallet_eth import EthereumChainCollector
from .web_profile import WebProfileCollector
from .whois_rdap import WhoisRdapCollector

__all__ = [
    "BitcoinChainCollector",
    "Collector",
    "EthereumChainCollector",
    "GitHubCollector",
    "PhoneCollector",
    "WebProfileCollector",
    "WhoisRdapCollector",
]
