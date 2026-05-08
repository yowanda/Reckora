"""Solana wallet collector backed by the public mainnet-beta JSON-RPC.

Solana exposes a uniform JSON-RPC API across every node. The public
endpoint at ``https://api.mainnet-beta.solana.com`` is free, key-less and
sufficient for OSINT-style account lookups; production-scale usage should
point at a dedicated provider (Helius, QuickNode, Triton) via
``SOLANA_RPC_URL``, but the collector functions out of the box on hosts
that have never seen a credential.

The collector emits a single normalised :class:`Trace` per supported
identifier with the high-signal fields the correlation engine and dossier
renderers want without parsing the raw envelope at render time:

- ``address`` — base58-encoded ed25519 pubkey, case preserved (Solana is
  case-sensitive)
- ``chain`` — always ``"solana"`` for this collector
- ``network`` — always ``"mainnet-beta"``
- ``address_format`` — always ``"ed25519"`` so a future Sui / Aptos
  collector that also leans on ed25519 keys can adopt the same string
- ``balance_lamports`` / ``balance_sol`` — current account balance in
  lamports (int) and as a string-formatted SOL amount with full
  9-decimal precision (no float drift)
- ``has_recent_activity`` — ``True`` when the account has at least one
  recent on-chain signature (queried via ``getSignaturesForAddress``
  with ``limit=1``); the absence of activity is itself an intelligence
  finding rather than a collection failure
- ``latest_signature`` / ``latest_block_time`` — most recent signature
  base58 string and its UTC ISO 8601 timestamp; both ``None`` when
  ``has_recent_activity`` is ``False``
- ``is_active`` — ``True`` iff ``balance_lamports > 0`` OR
  ``has_recent_activity``

The collector deliberately drops the raw HTTP envelopes from inline
evidence (``keep_raw=False``) — the SHA-256 of the canonicalised
combined payload is preserved so the chain stays auditable, but the
on-chain stats only surface in the normalised ``Trace.fields`` schema.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

SOLANA_RPC_BASE = "https://api.mainnet-beta.solana.com"
SOLANA_PROFILE_BASE = "https://explorer.solana.com/address"

_LAMPORTS_PER_SOL = 1_000_000_000

# Base58 alphabet (Bitcoin / Solana flavour — excludes 0, O, I, l to avoid
# visual collisions). Solana addresses are always base58-encoded
# ed25519 public keys (32 bytes → 43-44 base58 chars).
_BASE58_ALPHABET = frozenset("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def _is_solana_address(value: str) -> bool:
    """Return True iff ``value`` looks like a Solana mainnet pubkey.

    Cheap shape check, not a base58 decoder — we only need enough signal
    to (a) skip non-Solana strings before hitting the network and (b)
    avoid colliding with Bitcoin's base58 P2PKH/P2SH classifier (which
    caps at 35 chars). The 36-char lower bound guarantees disjoint
    matching: BTC P2PKH/P2SH ≤ 35, BTC bech32 starts with ``bc1``,
    Ethereum starts with ``0x`` and is 42 chars.
    """
    if not value:
        return False
    # Solana ed25519 pubkeys round-trip to 32-44 base58 chars; 43-44 is
    # overwhelmingly typical, but the 36-char floor is what makes the
    # check disjoint from Bitcoin base58 addresses.
    if not (36 <= len(value) <= 44):
        return False
    if value.startswith("0x"):
        return False
    if value.lower().startswith("bc1"):
        return False
    return set(value).issubset(_BASE58_ALPHABET)


def _format_sol(lamports: int) -> str:
    """Render a lamports amount as a SOL string with full 9-decimal precision.

    Floats can't represent 9 decimals exactly without drift, so we render
    via integer division. The result is canonicalised into the evidence
    hash and the dossier; ``"0.000000000"`` stays stable forever.
    """
    sign = "-" if lamports < 0 else ""
    abs_l = abs(lamports)
    whole, frac = divmod(abs_l, _LAMPORTS_PER_SOL)
    return f"{sign}{whole}.{frac:09d}"


class SolanaChainCollector(Collector):
    """Collect on-chain stats for a Solana mainnet-beta account.

    Parameters
    ----------
    rpc_url:
        Override for the JSON-RPC endpoint. Defaults to the public
        mainnet-beta cluster; a dedicated provider URL (Helius / QuickNode
        / Triton) is the recommended production setup but is not
        required.
    client:
        Optional pre-configured ``httpx.AsyncClient`` (used by the
        orchestrator / tests to share a single client and inject mocks).
    user_agent:
        Sent on every request. Defaults to ``"Reckora/0.1"``.
    """

    name: ClassVar[str] = "wallet_solana"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.WALLET.value})

    def __init__(
        self,
        *,
        rpc_url: str = SOLANA_RPC_BASE,
        client: httpx.AsyncClient | None = None,
        user_agent: str = "Reckora/0.1",
    ) -> None:
        super().__init__(client)
        self._rpc_url = rpc_url
        self._user_agent = user_agent

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _rpc(
        self,
        client: httpx.AsyncClient,
        method: str,
        params: list[Any],
        request_id: int,
    ) -> Any:
        body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        resp = await client.post(self._rpc_url, json=body, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        address = identifier.value.strip()
        if not _is_solana_address(address):
            # Not a Solana-shaped wallet — degrade silently so the BTC /
            # ETH adapters (and any future non-EVM L1 collector that
            # also handles IdentifierType.WALLET) still own their strings.
            return []

        client = await self._http()

        balance_raw = await self._rpc(client, "getBalance", [address], request_id=1)
        if _is_invalid_param_response(balance_raw):
            # The RPC rejected the address despite our shape check
            # passing — treat as no traces so the orchestrator's per-
            # collector try/except never has to swallow a 4xx.
            return []
        _raise_if_rate_limited(balance_raw)

        sigs_raw = await self._rpc(
            client,
            "getSignaturesForAddress",
            [address, {"limit": 1}],
            request_id=2,
        )
        _raise_if_rate_limited(sigs_raw)

        fields = self._normalise(address=address, balance_raw=balance_raw, sigs_raw=sigs_raw)

        # Use the human-facing Solana Explorer URL as the canonical
        # source URL on the evidence row — that is what an analyst would
        # paste into a dossier — and hash the combined RPC responses so
        # the audit trail covers every byte the collector actually saw.
        source_url = f"{SOLANA_PROFILE_BASE}/{address}"
        combined: dict[str, Any] = {"balance": balance_raw, "signatures": sigs_raw}
        evidence = make_evidence(source_url, combined, keep_raw=False)
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.WALLET_SOLANA,
                fields=fields,
                evidence=evidence,
            ),
        ]

    @staticmethod
    def _normalise(
        *,
        address: str,
        balance_raw: Any,
        sigs_raw: Any,
    ) -> dict[str, Any]:
        balance_lamports = 0
        if isinstance(balance_raw, dict):
            result = balance_raw.get("result")
            if isinstance(result, dict):
                balance_lamports = _coerce_int(result.get("value"))
            elif isinstance(result, int):
                # Some Solana clients flatten the result when ``encoding``
                # is omitted; tolerate the flat int shape too.
                balance_lamports = result

        latest_signature: str | None = None
        latest_block_time: str | None = None
        has_recent_activity = False
        if isinstance(sigs_raw, dict):
            sigs_result = sigs_raw.get("result")
            if isinstance(sigs_result, list) and sigs_result:
                head = sigs_result[0]
                if isinstance(head, dict):
                    has_recent_activity = True
                    sig = head.get("signature")
                    if isinstance(sig, str):
                        latest_signature = sig
                    block_time = head.get("blockTime")
                    if isinstance(block_time, int):
                        # Solana reports block_time as a unix timestamp.
                        # Normalising to ISO 8601 keeps dossier output
                        # human-readable and consistent with the rest of
                        # the engine.
                        latest_block_time = datetime.fromtimestamp(block_time, tz=UTC).isoformat()

        return {
            "address": address,
            "chain": "solana",
            "network": "mainnet-beta",
            "address_format": "ed25519",
            "balance_lamports": balance_lamports,
            "balance_sol": _format_sol(balance_lamports),
            "has_recent_activity": has_recent_activity,
            "latest_signature": latest_signature,
            "latest_block_time": latest_block_time,
            "is_active": balance_lamports > 0 or has_recent_activity,
        }


def _is_invalid_param_response(payload: Any) -> bool:
    """Heuristically detect a Solana JSON-RPC ``Invalid params`` error.

    Solana RPC reports user errors with an ``error`` object whose code
    is ``-32602`` and whose message starts with "Invalid params". We
    short-circuit only on this class of error; transient failures and
    rate-limit responses are surfaced upstream so the orchestrator can
    log them once.
    """
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    code = error.get("code")
    if code == -32602:
        return True
    message = error.get("message")
    return isinstance(message, str) and "invalid param" in message.lower()


def _raise_if_rate_limited(payload: Any) -> None:
    """Raise if the RPC reports the request was rate-limited.

    Public Solana RPCs return a 429-equivalent JSON-RPC error rather
    than an HTTP 429 in some configurations. We translate them into a
    runtime error so the orchestrator's per-collector logger surfaces
    them instead of silently emitting a Trace with zeroed-out fields.
    """
    if not isinstance(payload, dict):
        return
    error = payload.get("error")
    if not isinstance(error, dict):
        return
    code = error.get("code")
    if code == 429:
        message = error.get("message") or "rate limited"
        raise RuntimeError(f"solana rpc rate-limited: {message}")
    message = error.get("message")
    if isinstance(message, str):
        lowered = message.lower()
        if "rate limit" in lowered or "too many requests" in lowered:
            raise RuntimeError(f"solana rpc rate-limited: {message}")


def _coerce_int(value: Any) -> int:
    """Best-effort cast of a Solana RPC numeric field to ``int``."""
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
