"""Tests for the Ethereum wallet collector backed by Etherscan."""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.wallet_eth import (
    ETHERSCAN_API_BASE,
    ETHERSCAN_PROFILE_BASE,
    EthereumChainCollector,
    _format_eth,
    _is_evm_address,
)
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource

# Vitalik Buterin's well-known address — both forms below decode to the
# same on-chain bytes20; the mixed-case form carries an EIP-55 checksum.
VITALIK_LOWER = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"
VITALIK_CHECKSUMMED = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


@pytest.fixture
def collector() -> EthereumChainCollector:
    return EthereumChainCollector()


def test_is_evm_address_accepts_lowercase_and_checksummed() -> None:
    assert _is_evm_address(VITALIK_LOWER)
    assert _is_evm_address(VITALIK_CHECKSUMMED)
    assert _is_evm_address(ZERO_ADDRESS)


def test_is_evm_address_rejects_non_evm_strings() -> None:
    # Bitcoin legacy (P2PKH) — base58, not hex.
    assert not _is_evm_address("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
    # Bitcoin SegWit — bech32.
    assert not _is_evm_address("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
    # Missing 0x prefix.
    assert not _is_evm_address("d8da6bf26964af9d7eed9e03e53415d37aa96045")
    # Wrong length.
    assert not _is_evm_address("0xd8da6bf26964af9d7eed9e03e53415d37aa9604")
    assert not _is_evm_address("0xd8da6bf26964af9d7eed9e03e53415d37aa9604500")
    # Non-hex character inside the body.
    assert not _is_evm_address("0xZ8da6bf26964af9d7eed9e03e53415d37aa96045")
    # Empty / random.
    assert not _is_evm_address("")
    assert not _is_evm_address("alice")


def test_format_eth_renders_wei_with_eighteen_decimals() -> None:
    assert _format_eth(0) == "0.000000000000000000"
    assert _format_eth(1) == "0.000000000000000001"
    assert _format_eth(10**18) == "1.000000000000000000"
    # 1.5 ETH
    assert _format_eth(1_500_000_000_000_000_000) == "1.500000000000000000"
    assert _format_eth(-2_500_000_000_000_000_000) == "-2.500000000000000000"


async def test_collect_skips_unsupported_identifier(collector: EthereumChainCollector) -> None:
    ident = Identifier(type=IdentifierType.USERNAME, value="alice")
    assert await collector.collect(ident) == []


async def test_collect_skips_non_evm_wallet_string(collector: EthereumChainCollector) -> None:
    """A Bitcoin address must not trigger Etherscan calls."""
    ident = Identifier(
        type=IdentifierType.WALLET,
        value="1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
    )
    traces = await collector.collect(ident)
    assert traces == []


async def test_collect_normalises_etherscan_payload(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=(
            f"{ETHERSCAN_API_BASE}?module=account&action=balance&address={VITALIK_LOWER}&tag=latest"
        ),
        json={
            "status": "1",
            "message": "OK",
            "result": "1234567890123456789",
        },
    )
    httpx_mock.add_response(
        url=(
            f"{ETHERSCAN_API_BASE}?module=proxy&action=eth_getTransactionCount"
            f"&address={VITALIK_LOWER}&tag=latest"
        ),
        json={"jsonrpc": "2.0", "id": 1, "result": "0x4d2"},
    )

    ident = Identifier(type=IdentifierType.WALLET, value=VITALIK_CHECKSUMMED)
    async with httpx.AsyncClient() as client:
        collector = EthereumChainCollector(client=client)
        traces = await collector.collect(ident)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.identifier == ident
    assert trace.source == TraceSource.WALLET_ETHERSCAN

    fields = trace.fields
    # Canonicalised on the lowercase form so identifier joins are
    # case-insensitive; the user-supplied checksum survives in `address_input`.
    assert fields["address"] == VITALIK_LOWER
    assert fields["address_input"] == VITALIK_CHECKSUMMED
    assert fields["chain"] == "ethereum"
    assert fields["network"] == "mainnet"
    assert fields["address_format"] == "evm"
    assert fields["balance_wei"] == 1_234_567_890_123_456_789
    assert fields["balance_eth"] == "1.234567890123456789"
    assert fields["outgoing_tx_count"] == 0x4D2
    assert fields["is_active"] is True

    # Source URL on the evidence row points at the human-facing Etherscan
    # profile; the API URL is an implementation detail.
    assert trace.evidence.source_url == f"{ETHERSCAN_PROFILE_BASE}/{VITALIK_LOWER}"
    assert trace.evidence.raw_payload is None
    assert len(trace.evidence.payload_sha256) == 64


async def test_collect_marks_zero_balance_zero_nonce_inactive(httpx_mock: HTTPXMock) -> None:
    """A pristine address with no balance and no outgoing txs is `is_active=False`."""
    httpx_mock.add_response(
        url=(
            f"{ETHERSCAN_API_BASE}?module=account&action=balance&address={VITALIK_LOWER}&tag=latest"
        ),
        json={"status": "1", "message": "OK", "result": "0"},
    )
    httpx_mock.add_response(
        url=(
            f"{ETHERSCAN_API_BASE}?module=proxy&action=eth_getTransactionCount"
            f"&address={VITALIK_LOWER}&tag=latest"
        ),
        json={"jsonrpc": "2.0", "id": 1, "result": "0x0"},
    )

    ident = Identifier(type=IdentifierType.WALLET, value=VITALIK_LOWER)
    async with httpx.AsyncClient() as client:
        collector = EthereumChainCollector(client=client)
        traces = await collector.collect(ident)

    assert len(traces) == 1
    fields = traces[0].fields
    assert fields["balance_wei"] == 0
    assert fields["balance_eth"] == "0.000000000000000000"
    assert fields["outgoing_tx_count"] == 0
    assert fields["is_active"] is False


async def test_collect_marks_active_when_only_nonce_nonzero(httpx_mock: HTTPXMock) -> None:
    """A wallet that drained itself but has originated txs is still active."""
    httpx_mock.add_response(
        url=(
            f"{ETHERSCAN_API_BASE}?module=account&action=balance&address={VITALIK_LOWER}&tag=latest"
        ),
        json={"status": "1", "message": "OK", "result": "0"},
    )
    httpx_mock.add_response(
        url=(
            f"{ETHERSCAN_API_BASE}?module=proxy&action=eth_getTransactionCount"
            f"&address={VITALIK_LOWER}&tag=latest"
        ),
        json={"jsonrpc": "2.0", "id": 1, "result": "0x1"},
    )
    ident = Identifier(type=IdentifierType.WALLET, value=VITALIK_LOWER)
    async with httpx.AsyncClient() as client:
        collector = EthereumChainCollector(client=client)
        traces = await collector.collect(ident)

    fields = traces[0].fields
    assert fields["outgoing_tx_count"] == 1
    assert fields["is_active"] is True


async def test_collect_returns_empty_on_invalid_address_response(httpx_mock: HTTPXMock) -> None:
    """Etherscan rejects strings that pass our shape check but fail their validation."""
    httpx_mock.add_response(
        url=(
            f"{ETHERSCAN_API_BASE}?module=account&action=balance&address={VITALIK_LOWER}&tag=latest"
        ),
        json={
            "status": "0",
            "message": "NOTOK",
            "result": "Error! Invalid address format",
        },
    )
    ident = Identifier(type=IdentifierType.WALLET, value=VITALIK_LOWER)
    async with httpx.AsyncClient() as client:
        collector = EthereumChainCollector(client=client)
        traces = await collector.collect(ident)
    assert traces == []


async def test_collect_raises_on_rate_limit(httpx_mock: HTTPXMock) -> None:
    """Rate-limit responses are surfaced — the orchestrator's logger handles them."""
    httpx_mock.add_response(
        url=(
            f"{ETHERSCAN_API_BASE}?module=account&action=balance&address={VITALIK_LOWER}&tag=latest"
        ),
        json={
            "status": "0",
            "message": "NOTOK",
            "result": "Max rate limit reached, please use API Key for higher rate limit",
        },
    )
    ident = Identifier(type=IdentifierType.WALLET, value=VITALIK_LOWER)
    async with httpx.AsyncClient() as client:
        collector = EthereumChainCollector(client=client)
        with pytest.raises(RuntimeError, match="rate-limited"):
            await collector.collect(ident)


async def test_collect_raises_on_5xx(httpx_mock: HTTPXMock) -> None:
    """5xx is operational; the orchestrator's per-collector logger handles it."""
    httpx_mock.add_response(
        url=(
            f"{ETHERSCAN_API_BASE}?module=account&action=balance&address={VITALIK_LOWER}&tag=latest"
        ),
        status_code=502,
    )
    ident = Identifier(type=IdentifierType.WALLET, value=VITALIK_LOWER)
    async with httpx.AsyncClient() as client:
        collector = EthereumChainCollector(client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await collector.collect(ident)


async def test_collect_handles_malformed_nonce(httpx_mock: HTTPXMock) -> None:
    """A defensive cast — non-hex `result` from the proxy module shouldn't crash."""
    httpx_mock.add_response(
        url=(
            f"{ETHERSCAN_API_BASE}?module=account&action=balance&address={VITALIK_LOWER}&tag=latest"
        ),
        json={"status": "1", "message": "OK", "result": "1000"},
    )
    httpx_mock.add_response(
        url=(
            f"{ETHERSCAN_API_BASE}?module=proxy&action=eth_getTransactionCount"
            f"&address={VITALIK_LOWER}&tag=latest"
        ),
        json={"jsonrpc": "2.0", "id": 1, "result": "garbage"},
    )

    ident = Identifier(type=IdentifierType.WALLET, value=VITALIK_LOWER)
    async with httpx.AsyncClient() as client:
        collector = EthereumChainCollector(client=client)
        traces = await collector.collect(ident)

    fields = traces[0].fields
    assert fields["balance_wei"] == 1000
    assert fields["outgoing_tx_count"] == 0
    assert fields["is_active"] is True


async def test_collect_appends_apikey_when_configured(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=(
            f"{ETHERSCAN_API_BASE}?module=account&action=balance"
            f"&address={VITALIK_LOWER}&tag=latest&apikey=top-secret"
        ),
        json={"status": "1", "message": "OK", "result": "0"},
    )
    httpx_mock.add_response(
        url=(
            f"{ETHERSCAN_API_BASE}?module=proxy&action=eth_getTransactionCount"
            f"&address={VITALIK_LOWER}&tag=latest&apikey=top-secret"
        ),
        json={"jsonrpc": "2.0", "id": 1, "result": "0x0"},
    )

    ident = Identifier(type=IdentifierType.WALLET, value=VITALIK_LOWER)
    async with httpx.AsyncClient() as client:
        collector = EthereumChainCollector(client=client, api_key="top-secret")
        traces = await collector.collect(ident)
    assert len(traces) == 1


async def test_collect_sends_user_agent_and_accept_headers(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=(
            f"{ETHERSCAN_API_BASE}?module=account&action=balance&address={VITALIK_LOWER}&tag=latest"
        ),
        json={"status": "1", "message": "OK", "result": "0"},
        match_headers={
            "User-Agent": "Reckora/0.1",
            "Accept": "application/json",
        },
    )
    httpx_mock.add_response(
        url=(
            f"{ETHERSCAN_API_BASE}?module=proxy&action=eth_getTransactionCount"
            f"&address={VITALIK_LOWER}&tag=latest"
        ),
        json={"jsonrpc": "2.0", "id": 1, "result": "0x0"},
        match_headers={
            "User-Agent": "Reckora/0.1",
            "Accept": "application/json",
        },
    )
    ident = Identifier(type=IdentifierType.WALLET, value=VITALIK_LOWER)
    async with httpx.AsyncClient() as client:
        collector = EthereumChainCollector(client=client)
        traces = await collector.collect(ident)
    assert len(traces) == 1


async def test_supports_only_wallet_identifier(collector: EthereumChainCollector) -> None:
    assert collector.supports(Identifier(type=IdentifierType.WALLET, value=VITALIK_LOWER))
    assert not collector.supports(Identifier(type=IdentifierType.EMAIL, value="a@b.co"))
    assert not collector.supports(Identifier(type=IdentifierType.USERNAME, value="alice"))
