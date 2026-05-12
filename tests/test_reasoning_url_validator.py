"""Tests for the HEAD-probe URL validator and seed-in-body helper.

The validator is the single chokepoint that drops fake / hallucinated
URLs (LLM tools occasionally invent slugs that match a platform's
canonical regex but 404 in practice) and substring-matching noise
hits (``...elonmusk-pdf-free.html`` whose document is entirely
unrelated to Elon Musk) before they pollute the dossier.

We exercise it with ``pytest-httpx`` so no real network IO happens.
"""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.reasoning.url_validator import (
    URLProbeResult,
    probe_urls,
    verify_seed_in_body,
)


@pytest.mark.asyncio
async def test_probe_urls_keeps_live_drops_dead(httpx_mock: HTTPXMock) -> None:
    """200 / 3xx / 401 / 403 stay; 404 / 410 are dropped."""
    httpx_mock.add_response(url="https://example.com/alive", method="HEAD", status_code=200)
    httpx_mock.add_response(url="https://example.com/dead", method="HEAD", status_code=404)
    httpx_mock.add_response(url="https://example.com/gone", method="HEAD", status_code=410)
    httpx_mock.add_response(url="https://example.com/login", method="HEAD", status_code=401)
    httpx_mock.add_response(url="https://example.com/forbidden", method="HEAD", status_code=403)

    async with httpx.AsyncClient() as client:
        results = await probe_urls(
            [
                "https://example.com/alive",
                "https://example.com/dead",
                "https://example.com/gone",
                "https://example.com/login",
                "https://example.com/forbidden",
            ],
            client=client,
        )

    verdict = {r.url: r.alive for r in results}
    assert verdict == {
        "https://example.com/alive": True,
        "https://example.com/dead": False,
        "https://example.com/gone": False,
        "https://example.com/login": True,
        "https://example.com/forbidden": True,
    }


@pytest.mark.asyncio
async def test_probe_urls_falls_back_to_get_on_405(httpx_mock: HTTPXMock) -> None:
    """Cloudflare-default 405 on HEAD triggers an automatic GET retry."""
    httpx_mock.add_response(url="https://cdn.example/page", method="HEAD", status_code=405)
    httpx_mock.add_response(url="https://cdn.example/page", method="GET", status_code=200)

    async with httpx.AsyncClient() as client:
        results = await probe_urls(["https://cdn.example/page"], client=client)

    assert len(results) == 1
    assert results[0].alive is True
    assert results[0].http_status == 200


@pytest.mark.asyncio
async def test_probe_urls_transport_error_marks_dead(httpx_mock: HTTPXMock) -> None:
    """Connect timeouts / DNS failures are treated as not-alive."""
    httpx_mock.add_exception(httpx.ConnectTimeout("simulated DNS failure"))
    async with httpx.AsyncClient() as client:
        results = await probe_urls(["https://no-such-host.invalid"], client=client)

    assert results[0].alive is False
    assert results[0].http_status is None
    assert results[0].error == "ConnectTimeout"


@pytest.mark.asyncio
async def test_probe_urls_deduplicates_input(httpx_mock: HTTPXMock) -> None:
    """Duplicate URLs in the input list are only probed once."""
    httpx_mock.add_response(url="https://example.com/once", method="HEAD", status_code=200)

    async with httpx.AsyncClient() as client:
        results = await probe_urls(
            ["https://example.com/once", "https://example.com/once"],
            client=client,
        )

    # Only one probe issued; only one result returned.
    assert len(results) == 1
    assert isinstance(results[0], URLProbeResult)


@pytest.mark.asyncio
async def test_verify_seed_in_body_finds_in_title(httpx_mock: HTTPXMock) -> None:
    """Seed appearing in the page ``<title>`` is detected."""
    httpx_mock.add_response(
        url="https://example.com/doc",
        method="GET",
        text="<html><head><title>elonmusk leaked memo</title></head><body></body></html>",
    )
    async with httpx.AsyncClient() as client:
        verdict = await verify_seed_in_body(
            "https://example.com/doc",
            seed="elonmusk",
            client=client,
        )
    assert verdict is True


@pytest.mark.asyncio
async def test_verify_seed_in_body_finds_in_meta(httpx_mock: HTTPXMock) -> None:
    """Seed in an ``og:title`` meta tag is detected."""
    body = (
        '<html><head><meta property="og:title" content="elonmusk credentials dump">'
        "</head><body></body></html>"
    )
    httpx_mock.add_response(url="https://example.com/m", method="GET", text=body)
    async with httpx.AsyncClient() as client:
        verdict = await verify_seed_in_body(
            "https://example.com/m",
            seed="elonmusk",
            client=client,
        )
    assert verdict is True


@pytest.mark.asyncio
async def test_verify_seed_in_body_returns_false_when_absent(httpx_mock: HTTPXMock) -> None:
    """Seed absent from the sample → ``False`` (noise hit)."""
    httpx_mock.add_response(
        url="https://example.com/unrelated",
        method="GET",
        text="<html><head><title>Completely Different Document</title></head></html>",
    )
    async with httpx.AsyncClient() as client:
        verdict = await verify_seed_in_body(
            "https://example.com/unrelated",
            seed="elonmusk",
            client=client,
        )
    assert verdict is False


@pytest.mark.asyncio
async def test_verify_seed_in_body_returns_none_on_error(httpx_mock: HTTPXMock) -> None:
    """Transport error / non-2xx → ``None`` (caller falls back to URL signal)."""
    httpx_mock.add_response(url="https://example.com/500", method="GET", status_code=500)
    async with httpx.AsyncClient() as client:
        verdict = await verify_seed_in_body(
            "https://example.com/500",
            seed="elonmusk",
            client=client,
        )
    assert verdict is None
