"""Tests for the Solana wallet collector backed by the public JSON-RPC."""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.wallet_sol import (
    SOLANA_MAINNET_RPC,
    SolanaChainCollector,
    _format_sol,
    _is_solana_address,
)
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource

# Public well-known accounts (system program, Wrapped SOL mint, a stake
# account). All on mainnet; we don't actually call out to them in tests
# because we mock the RPC responses, but we use them as realistic shapes.
SYSTEM_PROGRAM = "11111111111111111111111111111111"
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"
RANDOM_STAKE = "DvHPsgs6JphTcsDoKQRfRrEMvUEWMGvNveEEyhFb6XEv"


@pytest.fixture
def collector() -> SolanaChainCollector:
    return SolanaChainCollector()


def test_is_solana_address_recognises_valid_shapes() -> None:
    assert _is_solana_address(SYSTEM_PROGRAM)
    assert _is_solana_address(WRAPPED_SOL_MINT)
    assert _is_solana_address(RANDOM_STAKE)


def test_is_solana_address_rejects_non_solana_strings() -> None:
    # Bitcoin legacy address — base58 alphabet but the wrong length envelope.
    assert not _is_solana_address("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
    # Ethereum hex — contains characters outside the base58 alphabet (0xl).
    assert not _is_solana_address("0xd8da6bf26964af9d7eed9e03e53415d37aa96045")
    # Empty string and obvious junk.
    assert not _is_solana_address("")
    assert not _is_solana_address("not-a-real-address!")
    # Contains an excluded base58 character ("0").
    assert not _is_solana_address("0vHPsgs6JphTcsDoKQRfRrEMvUEWMGvNveEEyhFb6XEv")


def test_format_sol_renders_lamports_with_nine_decimals() -> None:
    assert _format_sol(0) == "0.000000000"
    assert _format_sol(1) == "0.000000001"
    assert _format_sol(1_000_000_000) == "1.000000000"
    assert _format_sol(1_500_000_000) == "1.500000000"
    assert _format_sol(-250_000_000) == "-0.250000000"


async def test_collect_skips_unsupported_identifier(
    collector: SolanaChainCollector,
) -> None:
    ident = Identifier(type=IdentifierType.USERNAME, value="alice")
    assert await collector.collect(ident) == []


async def test_collect_skips_non_solana_wallet_string(
    collector: SolanaChainCollector,
) -> None:
    """Other wallet collectors own non-Solana strings — degrade silently."""
    ident = Identifier(
        type=IdentifierType.WALLET,
        value="0xd8da6bf26964af9d7eed9e03e53415d37aa96045",
    )
    assert await collector.collect(ident) == []


async def test_collect_normalises_active_account(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=SOLANA_MAINNET_RPC,
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "context": {"slot": 250_123_456},
                "value": 1_500_000_000,
            },
        },
        match_json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [RANDOM_STAKE],
        },
    )
    httpx_mock.add_response(
        url=SOLANA_MAINNET_RPC,
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "result": [
                {
                    "signature": "5VERv8NMvzbJMEkV8xnrLkEaWRtSz9CosKDYjCJjBRnbJLgp9k1ZMzKcA9zsWZE2k7VgRk",
                    "slot": 250_120_000,
                    "blockTime": 1_700_000_000,
                    "confirmationStatus": "finalized",
                    "err": None,
                },
            ],
        },
        match_json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "getSignaturesForAddress",
            "params": [RANDOM_STAKE, {"limit": 1}],
        },
    )

    ident = Identifier(type=IdentifierType.WALLET, value=RANDOM_STAKE)
    async with httpx.AsyncClient() as client:
        c = SolanaChainCollector(client=client)
        traces = await c.collect(ident)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.identifier == ident
    assert trace.source == TraceSource.WALLET_SOLANA

    fields = trace.fields
    assert fields["address"] == RANDOM_STAKE
    assert fields["chain"] == "solana"
    assert fields["network"] == "mainnet"
    assert fields["address_format"] == "ed25519"
    assert fields["balance_lamports"] == 1_500_000_000
    assert fields["balance_sol"] == "1.500000000"
    assert fields["latest_signature"] == (
        "5VERv8NMvzbJMEkV8xnrLkEaWRtSz9CosKDYjCJjBRnbJLgp9k1ZMzKcA9zsWZE2k7VgRk"
    )
    assert fields["latest_signature_block_time"] == 1_700_000_000
    assert fields["latest_signature_at"] == "2023-11-14T22:13:20Z"
    assert fields["is_active"] is True

    # Evidence is hashed but not inlined: RPC envelopes are large and we
    # only ever need the SHA for audit.
    assert trace.evidence.raw_payload is None
    assert len(trace.evidence.payload_sha256) == 64
    assert trace.evidence.source_url.endswith(f"/{RANDOM_STAKE}")


async def test_collect_emits_inactive_for_unseen_account(
    httpx_mock: HTTPXMock,
) -> None:
    """Zero balance + zero signatures => inactive account, but still a Trace."""
    httpx_mock.add_response(
        url=SOLANA_MAINNET_RPC,
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"context": {"slot": 1}, "value": 0},
        },
        match_json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [RANDOM_STAKE],
        },
    )
    httpx_mock.add_response(
        url=SOLANA_MAINNET_RPC,
        method="POST",
        json={"jsonrpc": "2.0", "id": 2, "result": []},
        match_json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "getSignaturesForAddress",
            "params": [RANDOM_STAKE, {"limit": 1}],
        },
    )

    ident = Identifier(type=IdentifierType.WALLET, value=RANDOM_STAKE)
    async with httpx.AsyncClient() as client:
        c = SolanaChainCollector(client=client)
        traces = await c.collect(ident)

    assert len(traces) == 1
    fields = traces[0].fields
    assert fields["balance_lamports"] == 0
    assert fields["balance_sol"] == "0.000000000"
    assert fields["latest_signature"] is None
    assert fields["latest_signature_block_time"] is None
    assert fields["latest_signature_at"] is None
    assert fields["is_active"] is False


async def test_collect_marks_funded_unspent_account_active(
    httpx_mock: HTTPXMock,
) -> None:
    """A funded account that's never been touched should still be flagged active."""
    httpx_mock.add_response(
        url=SOLANA_MAINNET_RPC,
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"context": {"slot": 1}, "value": 4_200_000_000},
        },
        match_json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [RANDOM_STAKE],
        },
    )
    httpx_mock.add_response(
        url=SOLANA_MAINNET_RPC,
        method="POST",
        json={"jsonrpc": "2.0", "id": 2, "result": []},
        match_json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "getSignaturesForAddress",
            "params": [RANDOM_STAKE, {"limit": 1}],
        },
    )
    ident = Identifier(type=IdentifierType.WALLET, value=RANDOM_STAKE)
    async with httpx.AsyncClient() as client:
        c = SolanaChainCollector(client=client)
        traces = await c.collect(ident)

    fields = traces[0].fields
    assert fields["balance_lamports"] == 4_200_000_000
    assert fields["balance_sol"] == "4.200000000"
    assert fields["latest_signature"] is None
    assert fields["is_active"] is True


async def test_collect_handles_missing_block_time(httpx_mock: HTTPXMock) -> None:
    """Very old leader-skipped slots can lose blockTime — should not crash."""
    httpx_mock.add_response(
        url=SOLANA_MAINNET_RPC,
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"context": {"slot": 1}, "value": 1},
        },
        match_json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [RANDOM_STAKE],
        },
    )
    httpx_mock.add_response(
        url=SOLANA_MAINNET_RPC,
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "result": [
                {
                    "signature": "5xx",
                    "slot": 1,
                    "blockTime": None,
                    "err": None,
                },
            ],
        },
        match_json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "getSignaturesForAddress",
            "params": [RANDOM_STAKE, {"limit": 1}],
        },
    )
    ident = Identifier(type=IdentifierType.WALLET, value=RANDOM_STAKE)
    async with httpx.AsyncClient() as client:
        c = SolanaChainCollector(client=client)
        traces = await c.collect(ident)

    fields = traces[0].fields
    assert fields["latest_signature"] == "5xx"
    assert fields["latest_signature_block_time"] is None
    assert fields["latest_signature_at"] is None
    assert fields["is_active"] is True  # signature exists


async def test_collect_returns_empty_on_invalid_param(httpx_mock: HTTPXMock) -> None:
    """RPC -32602 means "address rejected at decode time" — degrade silently."""
    httpx_mock.add_response(
        url=SOLANA_MAINNET_RPC,
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32602,
                "message": "Invalid param: WrongSize",
            },
        },
    )
    ident = Identifier(type=IdentifierType.WALLET, value=RANDOM_STAKE)
    async with httpx.AsyncClient() as client:
        c = SolanaChainCollector(client=client)
        traces = await c.collect(ident)
    assert traces == []


async def test_collect_raises_on_other_rpc_errors(httpx_mock: HTTPXMock) -> None:
    """Non-`-32602` RPC errors are operational; surface them upstream."""
    httpx_mock.add_response(
        url=SOLANA_MAINNET_RPC,
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32005, "message": "Node is behind"},
        },
    )
    ident = Identifier(type=IdentifierType.WALLET, value=RANDOM_STAKE)
    async with httpx.AsyncClient() as client:
        c = SolanaChainCollector(client=client)
        with pytest.raises(RuntimeError, match="solana RPC error"):
            await c.collect(ident)


async def test_collect_raises_on_5xx(httpx_mock: HTTPXMock) -> None:
    """5xx is operational; the orchestrator's per-collector logger handles it."""
    httpx_mock.add_response(
        url=SOLANA_MAINNET_RPC,
        method="POST",
        status_code=502,
    )
    ident = Identifier(type=IdentifierType.WALLET, value=RANDOM_STAKE)
    async with httpx.AsyncClient() as client:
        c = SolanaChainCollector(client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await c.collect(ident)


async def test_collect_sends_user_agent_and_content_type(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=SOLANA_MAINNET_RPC,
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"context": {"slot": 1}, "value": 0},
        },
        match_headers={
            "User-Agent": "Reckora/0.1",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        match_json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [RANDOM_STAKE],
        },
    )
    httpx_mock.add_response(
        url=SOLANA_MAINNET_RPC,
        method="POST",
        json={"jsonrpc": "2.0", "id": 2, "result": []},
    )
    ident = Identifier(type=IdentifierType.WALLET, value=RANDOM_STAKE)
    async with httpx.AsyncClient() as client:
        c = SolanaChainCollector(client=client)
        traces = await c.collect(ident)
    assert len(traces) == 1


async def test_supports_only_wallet_identifier(collector: SolanaChainCollector) -> None:
    assert collector.supports(Identifier(type=IdentifierType.WALLET, value=RANDOM_STAKE))
    assert not collector.supports(Identifier(type=IdentifierType.EMAIL, value="a@b.co"))
    assert not collector.supports(Identifier(type=IdentifierType.PHONE, value="+12025550123"))
