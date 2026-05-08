"""Tests for the Solana wallet collector backed by the mainnet-beta JSON-RPC."""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.wallet_sol import (
    SOLANA_PROFILE_BASE,
    SOLANA_RPC_BASE,
    SolanaChainCollector,
    _format_sol,
    _is_solana_address,
)
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource

# A well-known Solana Foundation address (43 base58 chars).
SOL_ADDRESS = "GThUX1Atko4tqhN2NaiTazWSeFWMuiUiswQrAogTcjQ"
# Another commonly cited address (44 chars) — Vote111111111111111111111111111111111111111
ALT_ADDRESS = "Vote111111111111111111111111111111111111111"


@pytest.fixture
def collector() -> SolanaChainCollector:
    return SolanaChainCollector()


def test_is_solana_address_accepts_typical_pubkeys() -> None:
    assert _is_solana_address(SOL_ADDRESS)
    assert _is_solana_address(ALT_ADDRESS)


def test_is_solana_address_rejects_other_chains_and_garbage() -> None:
    # Bitcoin legacy P2PKH (35 chars max — disjoint from Solana's 36-44 floor).
    assert not _is_solana_address("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
    # Bitcoin P2SH.
    assert not _is_solana_address("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy")
    # Bitcoin SegWit (bech32, starts with `bc1`).
    assert not _is_solana_address("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
    # Ethereum (starts with `0x`).
    assert not _is_solana_address("0xd8da6bf26964af9d7eed9e03e53415d37aa96045")
    # Empty / random / wrong length.
    assert not _is_solana_address("")
    assert not _is_solana_address("alice")
    # Contains a forbidden base58 character (zero).
    assert not _is_solana_address("0" * 44)


def test_format_sol_renders_lamports_with_nine_decimals() -> None:
    assert _format_sol(0) == "0.000000000"
    assert _format_sol(1) == "0.000000001"
    assert _format_sol(1_000_000_000) == "1.000000000"
    # 1.5 SOL
    assert _format_sol(1_500_000_000) == "1.500000000"
    assert _format_sol(-2_500_000_000) == "-2.500000000"


async def test_collect_skips_unsupported_identifier(collector: SolanaChainCollector) -> None:
    ident = Identifier(type=IdentifierType.USERNAME, value="alice")
    assert await collector.collect(ident) == []


async def test_collect_skips_non_solana_wallet_string(collector: SolanaChainCollector) -> None:
    """A Bitcoin / Ethereum address must not trigger Solana RPC calls."""
    btc_ident = Identifier(
        type=IdentifierType.WALLET,
        value="1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
    )
    eth_ident = Identifier(
        type=IdentifierType.WALLET,
        value="0xd8da6bf26964af9d7eed9e03e53415d37aa96045",
    )
    assert await collector.collect(btc_ident) == []
    assert await collector.collect(eth_ident) == []


async def test_collect_normalises_balance_and_signatures(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=SOLANA_RPC_BASE,
        method="POST",
        match_json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [SOL_ADDRESS],
        },
        json={
            "jsonrpc": "2.0",
            "result": {"context": {"slot": 1234}, "value": 2_500_000_000},
            "id": 1,
        },
    )
    httpx_mock.add_response(
        url=SOLANA_RPC_BASE,
        method="POST",
        match_json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "getSignaturesForAddress",
            "params": [SOL_ADDRESS, {"limit": 1}],
        },
        json={
            "jsonrpc": "2.0",
            "result": [
                {
                    "signature": "abc123sigsignaturebase58encoded",
                    "slot": 12345,
                    # 2024-06-01T12:00:00 UTC
                    "blockTime": 1_717_243_200,
                    "confirmationStatus": "finalized",
                    "err": None,
                }
            ],
            "id": 2,
        },
    )

    ident = Identifier(type=IdentifierType.WALLET, value=SOL_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = SolanaChainCollector(client=client)
        traces = await collector.collect(ident)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.identifier == ident
    assert trace.source == TraceSource.WALLET_SOLANA

    fields = trace.fields
    assert fields["address"] == SOL_ADDRESS
    assert fields["chain"] == "solana"
    assert fields["network"] == "mainnet-beta"
    assert fields["address_format"] == "ed25519"
    assert fields["balance_lamports"] == 2_500_000_000
    assert fields["balance_sol"] == "2.500000000"
    assert fields["has_recent_activity"] is True
    assert fields["latest_signature"] == "abc123sigsignaturebase58encoded"
    assert fields["latest_block_time"] == "2024-06-01T12:00:00+00:00"
    assert fields["is_active"] is True

    # Source URL points at the Solana Explorer profile; raw envelopes
    # are dropped but the SHA covers both RPC responses.
    assert trace.evidence.source_url == f"{SOLANA_PROFILE_BASE}/{SOL_ADDRESS}"
    assert trace.evidence.raw_payload is None
    assert len(trace.evidence.payload_sha256) == 64


async def test_collect_marks_dormant_account_inactive(httpx_mock: HTTPXMock) -> None:
    """A pristine address with zero balance and no signatures is `is_active=False`."""
    httpx_mock.add_response(
        url=SOLANA_RPC_BASE,
        method="POST",
        match_json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [SOL_ADDRESS],
        },
        json={
            "jsonrpc": "2.0",
            "result": {"context": {"slot": 1}, "value": 0},
            "id": 1,
        },
    )
    httpx_mock.add_response(
        url=SOLANA_RPC_BASE,
        method="POST",
        match_json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "getSignaturesForAddress",
            "params": [SOL_ADDRESS, {"limit": 1}],
        },
        json={"jsonrpc": "2.0", "result": [], "id": 2},
    )

    ident = Identifier(type=IdentifierType.WALLET, value=SOL_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = SolanaChainCollector(client=client)
        traces = await collector.collect(ident)

    assert len(traces) == 1
    fields = traces[0].fields
    assert fields["balance_lamports"] == 0
    assert fields["balance_sol"] == "0.000000000"
    assert fields["has_recent_activity"] is False
    assert fields["latest_signature"] is None
    assert fields["latest_block_time"] is None
    assert fields["is_active"] is False


async def test_collect_marks_active_when_only_balance_nonzero(
    httpx_mock: HTTPXMock,
) -> None:
    """A funded address with no signatures is still active."""
    httpx_mock.add_response(
        url=SOLANA_RPC_BASE,
        method="POST",
        match_json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [SOL_ADDRESS],
        },
        json={
            "jsonrpc": "2.0",
            "result": {"context": {"slot": 1}, "value": 12345},
            "id": 1,
        },
    )
    httpx_mock.add_response(
        url=SOLANA_RPC_BASE,
        method="POST",
        match_json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "getSignaturesForAddress",
            "params": [SOL_ADDRESS, {"limit": 1}],
        },
        json={"jsonrpc": "2.0", "result": [], "id": 2},
    )
    ident = Identifier(type=IdentifierType.WALLET, value=SOL_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = SolanaChainCollector(client=client)
        traces = await collector.collect(ident)
    fields = traces[0].fields
    assert fields["balance_lamports"] == 12345
    assert fields["has_recent_activity"] is False
    assert fields["is_active"] is True


async def test_collect_returns_empty_on_invalid_params(httpx_mock: HTTPXMock) -> None:
    """The RPC rejects strings that pass our shape check but aren't valid pubkeys."""
    httpx_mock.add_response(
        url=SOLANA_RPC_BASE,
        method="POST",
        match_json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [SOL_ADDRESS],
        },
        json={
            "jsonrpc": "2.0",
            "error": {"code": -32602, "message": "Invalid params: not a valid pubkey"},
            "id": 1,
        },
    )
    ident = Identifier(type=IdentifierType.WALLET, value=SOL_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = SolanaChainCollector(client=client)
        traces = await collector.collect(ident)
    assert traces == []


async def test_collect_raises_on_rate_limit_error(httpx_mock: HTTPXMock) -> None:
    """JSON-RPC rate-limit errors surface so the orchestrator logs them."""
    httpx_mock.add_response(
        url=SOLANA_RPC_BASE,
        method="POST",
        match_json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [SOL_ADDRESS],
        },
        json={
            "jsonrpc": "2.0",
            "error": {"code": 429, "message": "Too many requests"},
            "id": 1,
        },
    )
    ident = Identifier(type=IdentifierType.WALLET, value=SOL_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = SolanaChainCollector(client=client)
        with pytest.raises(RuntimeError, match="rate-limited"):
            await collector.collect(ident)


async def test_collect_raises_on_5xx(httpx_mock: HTTPXMock) -> None:
    """5xx is operational; the orchestrator's per-collector logger handles it."""
    httpx_mock.add_response(
        url=SOLANA_RPC_BASE,
        method="POST",
        status_code=502,
    )
    ident = Identifier(type=IdentifierType.WALLET, value=SOL_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = SolanaChainCollector(client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await collector.collect(ident)


async def test_collect_handles_flat_balance_int_result(httpx_mock: HTTPXMock) -> None:
    """Some clients flatten ``getBalance.result`` to a bare int."""
    httpx_mock.add_response(
        url=SOLANA_RPC_BASE,
        method="POST",
        match_json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [SOL_ADDRESS],
        },
        json={"jsonrpc": "2.0", "result": 7_777_777, "id": 1},
    )
    httpx_mock.add_response(
        url=SOLANA_RPC_BASE,
        method="POST",
        match_json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "getSignaturesForAddress",
            "params": [SOL_ADDRESS, {"limit": 1}],
        },
        json={"jsonrpc": "2.0", "result": [], "id": 2},
    )
    ident = Identifier(type=IdentifierType.WALLET, value=SOL_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = SolanaChainCollector(client=client)
        traces = await collector.collect(ident)
    fields = traces[0].fields
    assert fields["balance_lamports"] == 7_777_777
    assert fields["is_active"] is True


async def test_collect_handles_signatures_without_block_time(
    httpx_mock: HTTPXMock,
) -> None:
    """A signature without ``blockTime`` still flips `has_recent_activity` on."""
    httpx_mock.add_response(
        url=SOLANA_RPC_BASE,
        method="POST",
        match_json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [SOL_ADDRESS],
        },
        json={"jsonrpc": "2.0", "result": {"context": {"slot": 1}, "value": 0}, "id": 1},
    )
    httpx_mock.add_response(
        url=SOLANA_RPC_BASE,
        method="POST",
        match_json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "getSignaturesForAddress",
            "params": [SOL_ADDRESS, {"limit": 1}],
        },
        json={
            "jsonrpc": "2.0",
            "result": [{"signature": "sigOnly", "slot": 1, "err": None}],
            "id": 2,
        },
    )

    ident = Identifier(type=IdentifierType.WALLET, value=SOL_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = SolanaChainCollector(client=client)
        traces = await collector.collect(ident)
    fields = traces[0].fields
    assert fields["has_recent_activity"] is True
    assert fields["latest_signature"] == "sigOnly"
    assert fields["latest_block_time"] is None
    assert fields["is_active"] is True


async def test_collect_uses_custom_rpc_url(httpx_mock: HTTPXMock) -> None:
    custom_url = "https://example-rpc.invalid/sol"
    httpx_mock.add_response(
        url=custom_url,
        method="POST",
        match_json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [SOL_ADDRESS],
        },
        json={"jsonrpc": "2.0", "result": {"context": {"slot": 1}, "value": 0}, "id": 1},
    )
    httpx_mock.add_response(
        url=custom_url,
        method="POST",
        match_json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "getSignaturesForAddress",
            "params": [SOL_ADDRESS, {"limit": 1}],
        },
        json={"jsonrpc": "2.0", "result": [], "id": 2},
    )
    ident = Identifier(type=IdentifierType.WALLET, value=SOL_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = SolanaChainCollector(client=client, rpc_url=custom_url)
        traces = await collector.collect(ident)
    assert len(traces) == 1


async def test_collect_sends_user_agent_and_accept_headers(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=SOLANA_RPC_BASE,
        method="POST",
        json={"jsonrpc": "2.0", "result": {"context": {"slot": 1}, "value": 0}, "id": 1},
        match_headers={
            "User-Agent": "Reckora/0.1",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    httpx_mock.add_response(
        url=SOLANA_RPC_BASE,
        method="POST",
        json={"jsonrpc": "2.0", "result": [], "id": 2},
        match_headers={
            "User-Agent": "Reckora/0.1",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    ident = Identifier(type=IdentifierType.WALLET, value=SOL_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = SolanaChainCollector(client=client)
        traces = await collector.collect(ident)
    assert len(traces) == 1


async def test_supports_only_wallet_identifier(collector: SolanaChainCollector) -> None:
    assert collector.supports(Identifier(type=IdentifierType.WALLET, value=SOL_ADDRESS))
    assert not collector.supports(Identifier(type=IdentifierType.EMAIL, value="a@b.co"))
    assert not collector.supports(Identifier(type=IdentifierType.USERNAME, value="alice"))
