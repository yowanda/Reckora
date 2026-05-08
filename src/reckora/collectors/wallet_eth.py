"""Ethereum wallet collector backed by the Etherscan REST API.

`Etherscan <https://docs.etherscan.io/>`_ is a long-running indexer over
Ethereum mainnet that exposes per-account aggregate stats (current balance,
historical nonce, recent transactions) behind a stable REST surface. The
anonymous tier already covers a Reckora investigation's call budget — the
collector therefore works on hosts that have never seen a credential, but
will pick up an ``ETHERSCAN_API_KEY`` from the environment if one is
present (which lifts the rate limit and is the recommended production
setup).

The collector emits a single normalised :class:`Trace` per supported
identifier with the high-signal fields the correlation engine and dossier
renderers want without parsing the raw envelope at render time:

- ``address`` — input string, lower-cased so the canonicalised hash and
  any later identifier joins are case-insensitive (Ethereum's EIP-55
  checksum is purely a display convention; on-chain the address is
  bytes20)
- ``address_input`` — original case of the input, preserved verbatim for
  display so a user-supplied EIP-55 checksum survives round-tripping
- ``chain`` — always ``"ethereum"`` for this collector
- ``network`` — always ``"mainnet"``
- ``address_format`` — always ``"evm"``; the same string is shared across
  every EVM-compatible chain so a future Polygon / Arbitrum / Base
  adapter can reuse the schema unchanged
- ``balance_wei`` / ``balance_eth`` — current account balance in wei
  (int) and as a string-formatted ETH amount with full 18-decimal
  precision (no float drift)
- ``outgoing_tx_count`` — account nonce, which is exactly the number of
  external transactions the EOA has originated (does not include
  internal txs or inbound transfers)
- ``is_active`` — ``True`` iff the account holds a non-zero balance OR
  has originated at least one transaction. Wallets that have spent
  everything are still flagged active because the nonce is monotonic

The collector deliberately drops the raw HTTP envelopes from inline
evidence (``keep_raw=False``) — the SHA-256 of the canonicalised
combined payload is preserved so the chain stays auditable, but the
on-chain stats only surface in the normalised ``Trace.fields`` schema.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

ETHERSCAN_API_BASE = "https://api.etherscan.io/api"
ETHERSCAN_PROFILE_BASE = "https://etherscan.io/address"

_WEI_PER_ETH = 10**18
_HEX_ALPHABET = frozenset("0123456789abcdefABCDEF")


def _is_evm_address(value: str) -> bool:
    """Return True iff ``value`` looks like a 20-byte EVM hex address.

    A best-effort shape check, not an EIP-55 checksum verifier — we accept
    all-lowercase, all-uppercase and mixed-case forms (the latter being
    EIP-55-checksummed) and only reject strings whose body is not 40
    hex characters. The 0x prefix is required and case-sensitive.
    """
    if not value:
        return False
    if not value.startswith("0x"):
        return False
    body = value[2:]
    if len(body) != 40:
        return False
    return set(body).issubset(_HEX_ALPHABET)


def _format_eth(wei: int) -> str:
    """Render a wei amount as an ETH string with full 18-decimal precision.

    Floats can't represent 18 decimals exactly, so we render via integer
    division. The result is canonicalised into the evidence hash and the
    dossier; ``"0.000000000000000000"`` stays stable forever.
    """
    sign = "-" if wei < 0 else ""
    abs_wei = abs(wei)
    whole, frac = divmod(abs_wei, _WEI_PER_ETH)
    return f"{sign}{whole}.{frac:018d}"


class EthereumChainCollector(Collector):
    """Collect on-chain stats for an Ethereum mainnet account.

    Parameters
    ----------
    api_key:
        Optional Etherscan API key. When provided it is appended to every
        request as ``apikey=...`` so the call escapes the anonymous-tier
        rate limit. When unset the collector still functions on the
        public anonymous tier.
    client:
        Optional pre-configured ``httpx.AsyncClient`` (used by the
        orchestrator / tests to share a single client and inject mocks).
    user_agent:
        Sent on every request. Defaults to ``"Reckora/0.1"``.
    base_url:
        Override for tests; defaults to the production Etherscan host.
    """

    name: ClassVar[str] = "wallet_etherscan"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.WALLET.value})

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        user_agent: str = "Reckora/0.1",
        base_url: str = ETHERSCAN_API_BASE,
    ) -> None:
        super().__init__(client)
        self._api_key = api_key
        self._user_agent = user_agent
        self._base_url = base_url

    def _params(self, **extra: str) -> dict[str, str]:
        params: dict[str, str] = {**extra}
        if self._api_key:
            params["apikey"] = self._api_key
        return params

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        }

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        raw_address = identifier.value.strip()
        if not _is_evm_address(raw_address):
            # Not an EVM-shaped wallet — degrade silently so the BTC
            # collector (and any future Solana / Cosmos adapter that
            # also handles IdentifierType.WALLET) still owns its strings.
            return []

        # We canonicalise on the lowercase form: Ethereum addresses are
        # bytes20 on chain, the EIP-55 checksum is purely a display
        # convention. Storing lowercase keeps later identifier joins
        # case-insensitive without losing the user-supplied case.
        address = raw_address.lower()

        client = await self._http()
        balance_url = self._base_url
        balance_resp = await client.get(
            balance_url,
            params=self._params(
                module="account",
                action="balance",
                address=address,
                tag="latest",
            ),
            headers=self._headers(),
        )
        balance_resp.raise_for_status()
        balance_raw = balance_resp.json()
        if isinstance(balance_raw, dict) and _is_invalid_address_response(balance_raw):
            # Etherscan rejected the address despite our shape check
            # passing. Treat as "no traces" so the orchestrator's
            # per-collector try/except never has to swallow a 4xx.
            return []
        # Surface rate-limit / quota errors so the orchestrator's
        # per-collector logger records them once and the investigation
        # continues without this collector's data.
        _raise_if_rate_limited(balance_raw)

        nonce_resp = await client.get(
            self._base_url,
            params=self._params(
                module="proxy",
                action="eth_getTransactionCount",
                address=address,
                tag="latest",
            ),
            headers=self._headers(),
        )
        nonce_resp.raise_for_status()
        nonce_raw = nonce_resp.json()

        fields = self._normalise(
            address=address,
            address_input=raw_address,
            balance_raw=balance_raw,
            nonce_raw=nonce_raw,
        )

        # We use the human-facing Etherscan profile URL as the canonical
        # source URL on the evidence row — that is what an analyst would
        # paste into a dossier — and hash the combined API responses so
        # the audit trail covers every byte the collector actually saw.
        source_url = f"{ETHERSCAN_PROFILE_BASE}/{address}"
        combined: dict[str, Any] = {"balance": balance_raw, "nonce": nonce_raw}
        evidence = make_evidence(source_url, combined, keep_raw=False)
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.WALLET_ETHERSCAN,
                fields=fields,
                evidence=evidence,
            ),
        ]

    @staticmethod
    def _normalise(
        *,
        address: str,
        address_input: str,
        balance_raw: Any,
        nonce_raw: Any,
    ) -> dict[str, Any]:
        balance_wei = 0
        if isinstance(balance_raw, dict) and str(balance_raw.get("status")) == "1":
            balance_wei = _coerce_int(balance_raw.get("result"))

        outgoing_tx_count = 0
        if isinstance(nonce_raw, dict):
            result = nonce_raw.get("result")
            if isinstance(result, str) and result.startswith("0x"):
                try:
                    outgoing_tx_count = int(result, 16)
                except ValueError:
                    outgoing_tx_count = 0

        return {
            "address": address,
            "address_input": address_input,
            "chain": "ethereum",
            "network": "mainnet",
            "address_format": "evm",
            "balance_wei": balance_wei,
            "balance_eth": _format_eth(balance_wei),
            "outgoing_tx_count": outgoing_tx_count,
            "is_active": balance_wei > 0 or outgoing_tx_count > 0,
        }


def _is_invalid_address_response(payload: dict[str, Any]) -> bool:
    """Heuristically detect Etherscan's "Invalid address format" response.

    Etherscan reports user errors with a 200 OK body shaped
    ``{"status":"0","message":"NOTOK","result":"Error! Invalid address format"}``.
    We only short-circuit on this exact class of error; transient /
    rate-limit failures are surfaced upstream so the orchestrator can
    log them once.
    """
    if str(payload.get("status")) != "0":
        return False
    result = payload.get("result")
    if not isinstance(result, str):
        return False
    return "invalid address" in result.lower()


def _raise_if_rate_limited(payload: Any) -> None:
    """Raise if Etherscan reports the request was rate-limited / quota'd.

    The HTTP layer cannot detect this on its own — Etherscan returns
    200 OK with a ``status="0"`` body. We translate it into a runtime
    error so the orchestrator's per-collector logger surfaces it
    instead of silently emitting a Trace with zeroed-out fields.
    """
    if not isinstance(payload, dict):
        return
    if str(payload.get("status")) != "0":
        return
    result = payload.get("result")
    if not isinstance(result, str):
        return
    lowered = result.lower()
    if "rate limit" in lowered or "max calls per" in lowered or "max rate" in lowered:
        raise RuntimeError(f"etherscan rate-limited: {result}")


def _coerce_int(value: Any) -> int:
    """Best-effort cast of an Etherscan numeric field to ``int``.

    Etherscan returns balances as decimal strings (because wei does not
    fit in a JSON number for whales). We tolerate ``int``, ``str`` and
    silently zero everything else.
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
