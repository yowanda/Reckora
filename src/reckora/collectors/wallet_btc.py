"""Bitcoin wallet collector backed by the Blockstream Esplora HTTP API.

`Blockstream Esplora <https://github.com/Blockstream/esplora/blob/master/API.md>`_
is a public, key-less HTTP gateway in front of an Esplora-indexed Bitcoin
node. It exposes per-address aggregate stats (funded / spent / mempool
sums, transaction counts) without requiring registration, which makes it a
clean default for the wallet collector — no API key plumbing, no rate-limit
secrets, deterministic on hosts that have never seen a credential.

The collector emits a single normalised :class:`Trace` per supported
identifier with the high-signal fields the correlation engine and dossier
renderers want without parsing the raw envelope at render time:

- ``address`` — original on-chain string (case preserved for legacy /
  bech32 addresses)
- ``chain`` — always ``"bitcoin"`` for this collector
- ``network`` — always ``"mainnet"``
- ``address_format`` — ``"p2pkh"`` (``1...``), ``"p2sh"`` (``3...``),
  ``"bech32"`` (``bc1q...`` SegWit v0), ``"bech32m"`` (``bc1p...`` Taproot)
  or ``None`` if the format could not be inferred
- ``confirmed_tx_count`` / ``mempool_tx_count`` / ``tx_count`` — totals
- ``total_received_satoshi`` / ``total_spent_satoshi`` — confirmed lifetime
  flow into / out of the address (both monotonic; balance is the diff)
- ``current_balance_satoshi`` / ``current_balance_btc`` — confirmed balance
  in satoshi and as a string-formatted BTC amount (avoiding float drift)
- ``mempool_balance_satoshi`` — net mempool flow (funded - spent), can be
  negative when more is leaving the address than entering it
- ``is_active`` — ``True`` iff the address has at least one confirmed or
  unconfirmed transaction

The collector deliberately drops the raw HTTP envelope from inline evidence
(``keep_raw=False``) — the SHA-256 of the canonicalised payload is still
preserved so the chain stays auditable, but the on-chain stats only
surface in the normalised ``Trace.fields`` schema.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

BLOCKSTREAM_API_BASE = "https://blockstream.info/api"

_SATOSHI_PER_BTC = 100_000_000

# Bech32 / bech32m use a fixed 32-character lowercase alphabet.
_BECH32_ALPHABET = frozenset("qpzry9x8gf2tvdw0s3jn54khce6mua7l")
# Base58 (legacy + p2sh) excludes 0, O, I, l to avoid visual collisions.
_BASE58_ALPHABET = frozenset("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def _classify_address(value: str) -> str | None:
    """Infer a Bitcoin mainnet address format from its leading characters.

    This is intentionally a best-effort *prefix* check, not a full base58check
    / bech32 decoder — we only need enough signal to (a) skip obviously
    non-Bitcoin strings before hitting the network and (b) tag the
    address format on the resulting Trace. Returns ``None`` when the
    string does not match any known mainnet pattern.
    """
    if not value:
        return None
    head = value[0]
    if head == "1" and 26 <= len(value) <= 35 and set(value).issubset(_BASE58_ALPHABET):
        return "p2pkh"
    if head == "3" and 26 <= len(value) <= 35 and set(value).issubset(_BASE58_ALPHABET):
        return "p2sh"
    lower = value.lower()
    if lower.startswith("bc1") and 14 <= len(lower) <= 90:
        # Bech32 strings are required to be all-lowercase OR all-uppercase per
        # BIP-173; mixed-case is a hard error. We accept either and tag by
        # the witness-version prefix.
        if value != value.lower() and value != value.upper():
            return None
        body = lower[3:]
        if not body or not set(body).issubset(_BECH32_ALPHABET):
            return None
        # Witness-version 0 (SegWit) addresses start with ``q`` or ``p``
        # after the ``bc1`` HRP+separator; v1 (Taproot) starts with ``p``.
        # Discriminate by length: P2WPKH=42, P2WSH=62 -> bech32; Taproot=62
        # but with a different witness program. The cheap check is the 4th
        # char of the lowercase form (witness version after the separator).
        witness_char = lower[3]
        if witness_char == "q":
            return "bech32"
        if witness_char == "p":
            return "bech32m"
        return "bech32"
    return None


def _is_supported_address(value: str) -> bool:
    """Return True iff ``value`` looks like a Bitcoin mainnet address."""
    return _classify_address(value) is not None


def _format_btc(satoshi: int) -> str:
    """Render a satoshi amount as a BTC string with full 8-decimal precision.

    We deliberately avoid floats: the balance is canonicalised into the
    evidence hash and rendered into the dossier, and float drift would
    cause both the hash and the human-readable amount to flap between
    runs. ``"0.00000000"`` stays stable forever.
    """
    sign = "-" if satoshi < 0 else ""
    abs_sats = abs(satoshi)
    whole, frac = divmod(abs_sats, _SATOSHI_PER_BTC)
    return f"{sign}{whole}.{frac:08d}"


class BitcoinChainCollector(Collector):
    """Collect on-chain stats for a Bitcoin mainnet address.

    Parameters
    ----------
    client:
        Optional pre-configured ``httpx.AsyncClient`` (used by the orchestrator
        / tests to share a single client and inject mocks).
    user_agent:
        Sent on every request. Defaults to ``"Reckora/0.1"`` to match the
        rest of the engine.
    base_url:
        Override for tests; defaults to the public Blockstream Esplora
        production host.
    """

    name: ClassVar[str] = "wallet_blockstream"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.WALLET.value})

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        user_agent: str = "Reckora/0.1",
        base_url: str = BLOCKSTREAM_API_BASE,
    ) -> None:
        super().__init__(client)
        self._user_agent = user_agent
        self._base_url = base_url.rstrip("/")

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        address = identifier.value.strip()
        address_format = _classify_address(address)
        if address_format is None:
            # Not a Bitcoin mainnet address — degrade silently so other
            # wallet collectors (e.g. a future Ethereum / Solana adapter
            # that also supports IdentifierType.WALLET) can still run.
            return []

        client = await self._http()
        url = f"{self._base_url}/address/{address}"
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        }
        resp = await client.get(url, headers=headers)

        # 404 is the documented "address not seen on chain" response. We
        # still emit a Trace because a clean (zero-tx) address is itself
        # an intelligence finding — the absence of activity is signal.
        if resp.status_code == 404:
            return [self._empty_trace(identifier=identifier, url=url, fmt=address_format)]
        # 400 covers "Invalid bitcoin address" — Blockstream rejects strings
        # that pass our cheap prefix check but fail full validation. Treat
        # that as "no traces" so the orchestrator's per-collector
        # try/except never has to swallow a 4xx.
        if resp.status_code == 400:
            return []
        # 5xx / 401 / 429 / etc. are operational problems — re-raise so the
        # orchestrator's per-collector logger records a single line and the
        # investigation continues without this collector's data.
        resp.raise_for_status()

        raw = resp.json()
        if not isinstance(raw, dict):
            return [self._empty_trace(identifier=identifier, url=url, fmt=address_format)]

        fields = self._normalise(address=address, address_format=address_format, raw=raw)
        evidence = make_evidence(url, raw, keep_raw=False)
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.WALLET_BLOCKSTREAM,
                fields=fields,
                evidence=evidence,
            ),
        ]

    @staticmethod
    def _empty_trace(*, identifier: Identifier, url: str, fmt: str) -> Trace:
        """Return the "no on-chain activity" Trace for a clean address."""
        fields: dict[str, Any] = {
            "address": identifier.value.strip(),
            "chain": "bitcoin",
            "network": "mainnet",
            "address_format": fmt,
            "confirmed_tx_count": 0,
            "mempool_tx_count": 0,
            "tx_count": 0,
            "total_received_satoshi": 0,
            "total_spent_satoshi": 0,
            "current_balance_satoshi": 0,
            "current_balance_btc": _format_btc(0),
            "mempool_balance_satoshi": 0,
            "is_active": False,
        }
        evidence = make_evidence(url, {"chain_stats": {}, "mempool_stats": {}}, keep_raw=False)
        return Trace(
            identifier=identifier,
            source=TraceSource.WALLET_BLOCKSTREAM,
            fields=fields,
            evidence=evidence,
        )

    @staticmethod
    def _normalise(
        *,
        address: str,
        address_format: str,
        raw: dict[str, Any],
    ) -> dict[str, Any]:
        chain = raw.get("chain_stats") or {}
        mempool = raw.get("mempool_stats") or {}
        if not isinstance(chain, dict):
            chain = {}
        if not isinstance(mempool, dict):
            mempool = {}

        confirmed_tx_count = _coerce_int(chain.get("tx_count"))
        mempool_tx_count = _coerce_int(mempool.get("tx_count"))
        funded = _coerce_int(chain.get("funded_txo_sum"))
        spent = _coerce_int(chain.get("spent_txo_sum"))
        mp_funded = _coerce_int(mempool.get("funded_txo_sum"))
        mp_spent = _coerce_int(mempool.get("spent_txo_sum"))
        balance = funded - spent
        mempool_balance = mp_funded - mp_spent

        return {
            "address": address,
            "chain": "bitcoin",
            "network": "mainnet",
            "address_format": address_format,
            "confirmed_tx_count": confirmed_tx_count,
            "mempool_tx_count": mempool_tx_count,
            "tx_count": confirmed_tx_count + mempool_tx_count,
            "total_received_satoshi": funded,
            "total_spent_satoshi": spent,
            "current_balance_satoshi": balance,
            "current_balance_btc": _format_btc(balance),
            "mempool_balance_satoshi": mempool_balance,
            "is_active": (confirmed_tx_count + mempool_tx_count) > 0,
        }


def _coerce_int(value: Any) -> int:
    """Best-effort cast of an Esplora numeric field to ``int``.

    Esplora always returns integers for these fields, but a defensive
    cast keeps the collector robust against gateway proxies that JSON-
    encode large integers as strings (and against test fixtures with
    typoed shapes).
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0
