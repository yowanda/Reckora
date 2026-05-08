"""Solana wallet collector backed by the public Solana JSON-RPC endpoint.

Solana's `mainnet-beta JSON-RPC <https://docs.solana.com/developing/clients/jsonrpc-api>`_
is a public, key-less HTTP gateway in front of a validator with full
transaction history. It exposes per-account state (current lamport balance
and recent transaction signatures) without requiring registration, which
makes it a clean default for the Solana wallet collector — no API key
plumbing, no rate-limit secrets, deterministic on hosts that have never
seen a credential.

The collector emits a single normalised :class:`Trace` per supported
identifier with the high-signal fields the correlation engine and dossier
renderers want without parsing the raw RPC envelopes at render time:

- ``address`` — original on-chain string (case preserved; Solana
  base58-encodes ed25519 public keys, the encoding is case-sensitive)
- ``chain`` — always ``"solana"`` for this collector
- ``network`` — always ``"mainnet"``
- ``address_format`` — always ``"ed25519"``; Solana addresses are
  base58-encoded 32-byte ed25519 public keys
- ``balance_lamports`` — current account balance in lamports (int)
- ``balance_sol`` — current account balance as a string-formatted SOL
  amount with full 9-decimal precision (no float drift)
- ``latest_signature`` — base58 signature of the most recent
  transaction touching the account, or ``None`` if the account has
  never appeared in a confirmed block
- ``latest_signature_block_time`` — Unix epoch seconds for the block
  containing ``latest_signature``, or ``None`` when Solana could not
  recover a block time (very old leader-skipped slots)
- ``latest_signature_at`` — ISO-8601 UTC string mirror of
  ``latest_signature_block_time`` for human-readable rendering, or
  ``None`` when the block time was unavailable
- ``is_active`` — ``True`` iff the account holds a non-zero balance
  OR has ever appeared as a signer / participant in a confirmed
  transaction. A funded account that has never been touched (rare but
  possible — the SPL token program creates such accounts during ATA
  initialisation) is therefore still flagged active

The collector deliberately drops the raw RPC envelopes from inline
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

SOLANA_MAINNET_RPC = "https://api.mainnet-beta.solana.com"
SOLANA_EXPLORER_BASE = "https://explorer.solana.com/address"

_LAMPORTS_PER_SOL = 1_000_000_000
# Base58 (Bitcoin / Solana) excludes 0, O, I, l to avoid visual collisions.
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_INDEX = {ch: i for i, ch in enumerate(_BASE58_ALPHABET)}


def _base58_decode(value: str) -> bytes | None:
    """Decode a base58-encoded string. Returns ``None`` if not valid base58.

    Pure-Python so the collector inherits no extra runtime deps. The
    output length tells us the on-chain key size: Solana ed25519 public
    keys are 32 bytes; Bitcoin legacy / P2SH addresses are 25 bytes.
    """
    if not value:
        return None
    n = 0
    for ch in value:
        idx = _BASE58_INDEX.get(ch)
        if idx is None:
            return None
        n = n * 58 + idx
    body = bytearray()
    while n > 0:
        n, mod = divmod(n, 256)
        body.append(mod)
    body.reverse()
    leading_zeros = 0
    for ch in value:
        if ch != "1":
            break
        leading_zeros += 1
    return bytes(b"\x00" * leading_zeros) + bytes(body)


def _is_solana_address(value: str) -> bool:
    """Return True iff ``value`` looks like a base58-encoded Solana public key.

    Solana addresses are base58-encoded 32-byte ed25519 public keys. We
    decode and require exactly 32 bytes, which (a) excludes Bitcoin
    legacy / P2SH addresses (which decode to 25 bytes) without resorting
    to length heuristics and (b) keeps the check pure-Python with no
    extra runtime deps. This is still a shape check, not a curve
    validator — we don't verify the bytes are a valid ed25519 point.
    """
    if not value:
        return False
    decoded = _base58_decode(value)
    if decoded is None:
        return False
    return len(decoded) == 32


def _format_sol(lamports: int) -> str:
    """Render a lamport amount as a SOL string with full 9-decimal precision.

    Floats can't represent 9 decimals exactly across the full lamport
    range, so we render via integer division. The result is canonicalised
    into the evidence hash and the dossier; ``"0.000000000"`` stays
    stable forever.
    """
    sign = "-" if lamports < 0 else ""
    abs_lamports = abs(lamports)
    whole, frac = divmod(abs_lamports, _LAMPORTS_PER_SOL)
    return f"{sign}{whole}.{frac:09d}"


class SolanaChainCollector(Collector):
    """Collect on-chain stats for a Solana mainnet account.

    Parameters
    ----------
    client:
        Optional pre-configured ``httpx.AsyncClient`` (used by the
        orchestrator / tests to share a single client and inject mocks).
    user_agent:
        Sent on every request. Defaults to ``"Reckora/0.1"``.
    rpc_url:
        Override for tests; defaults to the public Solana mainnet-beta
        JSON-RPC endpoint.
    """

    name: ClassVar[str] = "wallet_solana"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.WALLET.value})

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        user_agent: str = "Reckora/0.1",
        rpc_url: str = SOLANA_MAINNET_RPC,
    ) -> None:
        super().__init__(client)
        self._user_agent = user_agent
        self._rpc_url = rpc_url

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        address = identifier.value.strip()
        if not _is_solana_address(address):
            # Not a Solana mainnet address — degrade silently so the BTC
            # / Ethereum collectors still own their respective strings.
            return []

        client = await self._http()

        balance_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [address],
        }
        balance_resp = await client.post(
            self._rpc_url, json=balance_payload, headers=self._headers()
        )
        balance_resp.raise_for_status()
        balance_raw = balance_resp.json()
        if _is_invalid_param_error(balance_raw):
            # The Solana RPC rejected the address despite our shape check
            # passing (e.g. body of the right length but not a valid
            # ed25519 point). Treat as "no traces" so the orchestrator's
            # per-collector try/except never has to swallow a 4xx.
            return []
        _raise_if_rpc_error(balance_raw)

        signatures_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "getSignaturesForAddress",
            "params": [address, {"limit": 1}],
        }
        signatures_resp = await client.post(
            self._rpc_url, json=signatures_payload, headers=self._headers()
        )
        signatures_resp.raise_for_status()
        signatures_raw = signatures_resp.json()
        if _is_invalid_param_error(signatures_raw):
            return []
        _raise_if_rpc_error(signatures_raw)

        fields = self._normalise(
            address=address,
            balance_raw=balance_raw,
            signatures_raw=signatures_raw,
        )

        # We use the human-facing Solana Explorer URL as the canonical
        # source URL on the evidence row — that is what an analyst would
        # paste into a dossier — and hash the combined RPC responses so
        # the audit trail covers every byte the collector actually saw.
        source_url = f"{SOLANA_EXPLORER_BASE}/{address}"
        combined: dict[str, Any] = {
            "balance": balance_raw,
            "signatures": signatures_raw,
        }
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
        signatures_raw: Any,
    ) -> dict[str, Any]:
        balance_lamports = 0
        if isinstance(balance_raw, dict):
            result = balance_raw.get("result")
            if isinstance(result, dict):
                balance_lamports = _coerce_int(result.get("value"))

        latest_signature: str | None = None
        latest_block_time: int | None = None
        latest_at: str | None = None
        signature_count = 0
        if isinstance(signatures_raw, dict):
            sigs = signatures_raw.get("result")
            if isinstance(sigs, list) and sigs:
                signature_count = 1  # we only requested limit=1
                first = sigs[0]
                if isinstance(first, dict):
                    sig = first.get("signature")
                    if isinstance(sig, str) and sig:
                        latest_signature = sig
                    block_time = first.get("blockTime")
                    if isinstance(block_time, int):
                        latest_block_time = block_time
                        latest_at = (
                            datetime.fromtimestamp(block_time, tz=UTC)
                            .isoformat()
                            .replace("+00:00", "Z")
                        )

        return {
            "address": address,
            "chain": "solana",
            "network": "mainnet",
            "address_format": "ed25519",
            "balance_lamports": balance_lamports,
            "balance_sol": _format_sol(balance_lamports),
            "latest_signature": latest_signature,
            "latest_signature_block_time": latest_block_time,
            "latest_signature_at": latest_at,
            "is_active": balance_lamports > 0 or signature_count > 0,
        }


def _is_invalid_param_error(payload: Any) -> bool:
    """Detect Solana's "invalid param" JSON-RPC error.

    The RPC reports user errors with a 200 OK body shaped
    ``{"error":{"code":-32602,"message":"Invalid param: ..."}}``. We only
    short-circuit on this exact error code; transient or rate-limit
    failures surface upstream so the orchestrator's per-collector logger
    records them once.
    """
    if not isinstance(payload, dict):
        return False
    err = payload.get("error")
    if not isinstance(err, dict):
        return False
    return err.get("code") == -32602


def _raise_if_rpc_error(payload: Any) -> None:
    """Raise if Solana RPC reports an error other than invalid-param.

    The HTTP layer can't detect this on its own — Solana returns 200 OK
    with an ``error`` body. We translate it into a runtime error so the
    orchestrator's per-collector logger surfaces it instead of silently
    emitting a Trace with zeroed-out fields.
    """
    if not isinstance(payload, dict):
        return
    err = payload.get("error")
    if not isinstance(err, dict):
        return
    code = err.get("code")
    if code == -32602:  # handled separately as "no traces"
        return
    message = err.get("message") or "unknown solana RPC error"
    raise RuntimeError(f"solana RPC error {code}: {message}")


def _coerce_int(value: Any) -> int:
    """Best-effort cast of a Solana numeric field to ``int``.

    Lamports always fit in a JSON number (mainnet's total supply is well
    under 2**53), but a defensive cast keeps the collector robust against
    proxies that JSON-encode integers as strings.
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
