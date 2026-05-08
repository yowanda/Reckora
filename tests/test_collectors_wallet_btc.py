"""Tests for the Bitcoin wallet collector backed by Blockstream Esplora."""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.wallet_btc import (
    BLOCKSTREAM_API_BASE,
    BitcoinChainCollector,
    _classify_address,
    _format_btc,
)
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource

GENESIS_ADDRESS = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
SEGWIT_ADDRESS = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
TAPROOT_ADDRESS = "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr"
P2SH_ADDRESS = "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"


@pytest.fixture
def collector() -> BitcoinChainCollector:
    return BitcoinChainCollector()


def test_classify_address_recognises_known_formats() -> None:
    assert _classify_address(GENESIS_ADDRESS) == "p2pkh"
    assert _classify_address(P2SH_ADDRESS) == "p2sh"
    assert _classify_address(SEGWIT_ADDRESS) == "bech32"
    assert _classify_address(TAPROOT_ADDRESS) == "bech32m"


def test_classify_address_rejects_non_bitcoin_strings() -> None:
    # Ethereum hex address — superficially looks like a wallet but doesn't
    # match any Bitcoin prefix.
    assert _classify_address("0xd8da6bf26964af9d7eed9e03e53415d37aa96045") is None
    # Mixed-case bech32 is a hard error per BIP-173.
    assert _classify_address("Bc1qW508D6qejxtdG4y5r3zarvary0c5xw7kv8f3t4") is None
    # Non-base58 character (zero) inside a legacy-shaped string.
    assert _classify_address("10000000000000000000000000000000000") is None
    assert _classify_address("") is None


def test_format_btc_renders_satoshi_with_eight_decimals() -> None:
    assert _format_btc(0) == "0.00000000"
    assert _format_btc(1) == "0.00000001"
    assert _format_btc(100_000_000) == "1.00000000"
    assert _format_btc(150_000_000) == "1.50000000"
    assert _format_btc(-25_000_000) == "-0.25000000"


async def test_collect_skips_unsupported_identifier(collector: BitcoinChainCollector) -> None:
    ident = Identifier(type=IdentifierType.USERNAME, value="alice")
    assert await collector.collect(ident) == []


async def test_collect_skips_non_btc_wallet_string(collector: BitcoinChainCollector) -> None:
    """Other wallet collectors (e.g. a future Ethereum adapter) own non-BTC strings."""
    ident = Identifier(
        type=IdentifierType.WALLET,
        value="0xd8da6bf26964af9d7eed9e03e53415d37aa96045",
    )
    traces = await collector.collect(ident)
    assert traces == []


async def test_collect_normalises_blockstream_payload(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BLOCKSTREAM_API_BASE}/address/{GENESIS_ADDRESS}",
        json={
            "address": GENESIS_ADDRESS,
            "chain_stats": {
                "funded_txo_count": 250,
                "funded_txo_sum": 7_523_456_700,
                "spent_txo_count": 50,
                "spent_txo_sum": 1_000_000_000,
                "tx_count": 200,
            },
            "mempool_stats": {
                "funded_txo_count": 1,
                "funded_txo_sum": 50_000_000,
                "spent_txo_count": 0,
                "spent_txo_sum": 0,
                "tx_count": 1,
            },
        },
    )
    ident = Identifier(type=IdentifierType.WALLET, value=GENESIS_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = BitcoinChainCollector(client=client)
        traces = await collector.collect(ident)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.identifier == ident
    assert trace.source == TraceSource.WALLET_BLOCKSTREAM

    fields = trace.fields
    assert fields["address"] == GENESIS_ADDRESS
    assert fields["chain"] == "bitcoin"
    assert fields["network"] == "mainnet"
    assert fields["address_format"] == "p2pkh"
    assert fields["confirmed_tx_count"] == 200
    assert fields["mempool_tx_count"] == 1
    assert fields["tx_count"] == 201
    assert fields["total_received_satoshi"] == 7_523_456_700
    assert fields["total_spent_satoshi"] == 1_000_000_000
    assert fields["current_balance_satoshi"] == 6_523_456_700
    assert fields["current_balance_btc"] == "65.23456700"
    assert fields["mempool_balance_satoshi"] == 50_000_000
    assert fields["is_active"] is True

    # Evidence is hashed but not inlined: HTTP envelopes from public chain
    # explorers can be large and we only ever need the SHA for audit.
    assert trace.evidence.raw_payload is None
    assert len(trace.evidence.payload_sha256) == 64


async def test_collect_emits_clean_trace_for_unseen_address(httpx_mock: HTTPXMock) -> None:
    """A 404 means "address not seen on chain" — still an intelligence finding."""
    httpx_mock.add_response(
        url=f"{BLOCKSTREAM_API_BASE}/address/{SEGWIT_ADDRESS}",
        status_code=404,
    )
    ident = Identifier(type=IdentifierType.WALLET, value=SEGWIT_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = BitcoinChainCollector(client=client)
        traces = await collector.collect(ident)

    assert len(traces) == 1
    fields = traces[0].fields
    assert fields["address"] == SEGWIT_ADDRESS
    assert fields["address_format"] == "bech32"
    assert fields["tx_count"] == 0
    assert fields["confirmed_tx_count"] == 0
    assert fields["mempool_tx_count"] == 0
    assert fields["current_balance_satoshi"] == 0
    assert fields["current_balance_btc"] == "0.00000000"
    assert fields["is_active"] is False


async def test_collect_returns_empty_on_blockstream_400(httpx_mock: HTTPXMock) -> None:
    """Blockstream rejects strings that pass our cheap prefix check but fail validation."""
    httpx_mock.add_response(
        url=f"{BLOCKSTREAM_API_BASE}/address/{GENESIS_ADDRESS}",
        status_code=400,
        text="Invalid bitcoin address",
    )
    ident = Identifier(type=IdentifierType.WALLET, value=GENESIS_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = BitcoinChainCollector(client=client)
        traces = await collector.collect(ident)
    assert traces == []


async def test_collect_raises_on_5xx(httpx_mock: HTTPXMock) -> None:
    """5xx is operational; the orchestrator's per-collector logger handles it."""
    httpx_mock.add_response(
        url=f"{BLOCKSTREAM_API_BASE}/address/{GENESIS_ADDRESS}",
        status_code=502,
    )
    ident = Identifier(type=IdentifierType.WALLET, value=GENESIS_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = BitcoinChainCollector(client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await collector.collect(ident)


async def test_collect_handles_minimal_payload(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BLOCKSTREAM_API_BASE}/address/{TAPROOT_ADDRESS}",
        json={"address": TAPROOT_ADDRESS},
    )
    ident = Identifier(type=IdentifierType.WALLET, value=TAPROOT_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = BitcoinChainCollector(client=client)
        traces = await collector.collect(ident)

    assert len(traces) == 1
    fields = traces[0].fields
    assert fields["address_format"] == "bech32m"
    assert fields["tx_count"] == 0
    assert fields["total_received_satoshi"] == 0
    assert fields["total_spent_satoshi"] == 0
    assert fields["current_balance_satoshi"] == 0
    assert fields["current_balance_btc"] == "0.00000000"
    assert fields["is_active"] is False


async def test_collect_handles_non_dict_payload(httpx_mock: HTTPXMock) -> None:
    """A defensive cast — if Blockstream ever returns ``[]`` we don't crash."""
    httpx_mock.add_response(
        url=f"{BLOCKSTREAM_API_BASE}/address/{P2SH_ADDRESS}",
        json=[],
    )
    ident = Identifier(type=IdentifierType.WALLET, value=P2SH_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = BitcoinChainCollector(client=client)
        traces = await collector.collect(ident)

    assert len(traces) == 1
    assert traces[0].fields["address_format"] == "p2sh"
    assert traces[0].fields["is_active"] is False


async def test_collect_sends_user_agent_and_accept_headers(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BLOCKSTREAM_API_BASE}/address/{GENESIS_ADDRESS}",
        json={
            "chain_stats": {
                "funded_txo_count": 0,
                "funded_txo_sum": 0,
                "spent_txo_count": 0,
                "spent_txo_sum": 0,
                "tx_count": 0,
            },
            "mempool_stats": {
                "funded_txo_count": 0,
                "funded_txo_sum": 0,
                "spent_txo_count": 0,
                "spent_txo_sum": 0,
                "tx_count": 0,
            },
        },
        match_headers={
            "User-Agent": "Reckora/0.1",
            "Accept": "application/json",
        },
    )
    ident = Identifier(type=IdentifierType.WALLET, value=GENESIS_ADDRESS)
    async with httpx.AsyncClient() as client:
        collector = BitcoinChainCollector(client=client)
        traces = await collector.collect(ident)
    assert len(traces) == 1


async def test_supports_only_wallet_identifier(collector: BitcoinChainCollector) -> None:
    assert collector.supports(Identifier(type=IdentifierType.WALLET, value=GENESIS_ADDRESS))
    assert not collector.supports(Identifier(type=IdentifierType.EMAIL, value="a@b.co"))
    assert not collector.supports(Identifier(type=IdentifierType.PHONE, value="+12025550123"))
