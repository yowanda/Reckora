"""Tests for the avatar collector."""

from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image
from pytest_httpx import HTTPXMock

from reckora.collectors.avatar import (
    DEFAULT_MAX_BYTES,
    AvatarCollector,
    _is_http_url,
)
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource

AVATAR_URL = "https://example.com/avatars/alice.png"


def _png_bytes(*, color: tuple[int, int, int] = (255, 0, 0), size: int = 64) -> bytes:
    """Generate a deterministic in-memory PNG of the requested colour."""
    img = Image.new("RGB", (size, size), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def collector() -> AvatarCollector:
    return AvatarCollector()


def test_is_http_url_accepts_real_avatar_urls() -> None:
    assert _is_http_url("https://example.com/a.png")
    assert _is_http_url("http://avatars.githubusercontent.com/u/1?v=4")
    assert _is_http_url("HTTPS://EXAMPLE.COM/A.png")


def test_is_http_url_rejects_other_schemes_and_garbage() -> None:
    assert not _is_http_url("")
    assert not _is_http_url("alice")
    assert not _is_http_url("ftp://example.com/a.png")
    assert not _is_http_url("data:image/png;base64,iVBORw0KGgo=")
    assert not _is_http_url("file:///etc/passwd")
    assert not _is_http_url("https://")
    # No host segment => reject (treated as not-a-real-URL by our cheap shape check).
    assert not _is_http_url("https:///path")


async def test_collect_skips_unsupported_identifier(collector: AvatarCollector) -> None:
    ident = Identifier(type=IdentifierType.USERNAME, value="alice")
    assert await collector.collect(ident) == []


async def test_collect_skips_non_http_avatar_value(collector: AvatarCollector) -> None:
    ident = Identifier(
        type=IdentifierType.AVATAR,
        value="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA",
    )
    assert await collector.collect(ident) == []


async def test_collect_emits_trace_with_phash_for_png(httpx_mock: HTTPXMock) -> None:
    body = _png_bytes()
    httpx_mock.add_response(
        url=AVATAR_URL,
        method="GET",
        content=body,
        headers={"content-type": "image/png"},
    )

    ident = Identifier(type=IdentifierType.AVATAR, value=AVATAR_URL)
    async with httpx.AsyncClient() as client:
        collector = AvatarCollector(client=client)
        traces = await collector.collect(ident)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.identifier == ident
    assert trace.source == TraceSource.AVATAR_HTTP

    fields = trace.fields
    assert fields["url"] == AVATAR_URL
    assert fields["content_type"] == "image/png"
    assert fields["bytes_size"] == len(body)
    assert len(fields["bytes_sha256"]) == 64
    assert fields["width"] == 64
    assert fields["height"] == 64
    assert fields["mode"] == "RGB"
    assert fields["format"] == "PNG"
    # 64-bit dHash → 16 hex chars.
    assert isinstance(fields["avatar_phash"], str)
    assert len(fields["avatar_phash"]) == 16
    assert isinstance(fields["avatar_phash_perceptual"], str)
    assert len(fields["avatar_phash_perceptual"]) == 16
    assert isinstance(fields["avatar_ahash"], str)
    assert len(fields["avatar_ahash"]) == 16
    assert fields["is_active"] is True

    # Source URL on the evidence row IS the avatar URL; raw bytes are
    # never inlined into evidence.
    assert trace.evidence.source_url == AVATAR_URL
    assert trace.evidence.raw_payload is None
    assert len(trace.evidence.payload_sha256) == 64


async def test_collect_field_avatar_phash_is_compatible_with_correlation_rule(
    httpx_mock: HTTPXMock,
) -> None:
    """The dHash this collector emits must feed the avatar_phash rule directly.

    Two pixel-identical avatars hosted at different URLs should produce
    matching dHash hex strings — exactly the join key the existing
    rule needs.
    """
    body = _png_bytes(color=(10, 20, 30))
    httpx_mock.add_response(
        url=AVATAR_URL,
        method="GET",
        content=body,
        headers={"content-type": "image/png"},
    )
    other_url = "https://other.example.com/a.png"
    httpx_mock.add_response(
        url=other_url,
        method="GET",
        content=body,
        headers={"content-type": "image/png"},
    )

    async with httpx.AsyncClient() as client:
        collector = AvatarCollector(client=client)
        a = (
            await collector.collect(
                Identifier(type=IdentifierType.AVATAR, value=AVATAR_URL),
            )
        )[0]
        b = (
            await collector.collect(
                Identifier(type=IdentifierType.AVATAR, value=other_url),
            )
        )[0]

    assert a.fields["avatar_phash"] == b.fields["avatar_phash"]
    # Distinct URLs ⇒ distinct evidence URLs but identical content hash.
    assert a.fields["bytes_sha256"] == b.fields["bytes_sha256"]
    assert a.evidence.source_url != b.evidence.source_url


async def test_collect_returns_empty_on_404(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=AVATAR_URL, method="GET", status_code=404)
    ident = Identifier(type=IdentifierType.AVATAR, value=AVATAR_URL)
    async with httpx.AsyncClient() as client:
        collector = AvatarCollector(client=client)
        assert await collector.collect(ident) == []


async def test_collect_returns_empty_on_403(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=AVATAR_URL, method="GET", status_code=403)
    ident = Identifier(type=IdentifierType.AVATAR, value=AVATAR_URL)
    async with httpx.AsyncClient() as client:
        collector = AvatarCollector(client=client)
        assert await collector.collect(ident) == []


async def test_collect_raises_on_5xx(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=AVATAR_URL, method="GET", status_code=502)
    ident = Identifier(type=IdentifierType.AVATAR, value=AVATAR_URL)
    async with httpx.AsyncClient() as client:
        collector = AvatarCollector(client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await collector.collect(ident)


async def test_collect_skips_non_image_content_types(httpx_mock: HTTPXMock) -> None:
    """An HTML 200 (paywall, redirect to login) must not become a Trace."""
    httpx_mock.add_response(
        url=AVATAR_URL,
        method="GET",
        content=b"<html>login required</html>",
        headers={"content-type": "text/html; charset=utf-8"},
    )
    ident = Identifier(type=IdentifierType.AVATAR, value=AVATAR_URL)
    async with httpx.AsyncClient() as client:
        collector = AvatarCollector(client=client)
        assert await collector.collect(ident) == []


async def test_collect_skips_undecodable_bytes(httpx_mock: HTTPXMock) -> None:
    """Server claims image/* but the bytes don't decode."""
    httpx_mock.add_response(
        url=AVATAR_URL,
        method="GET",
        content=b"not really an image",
        headers={"content-type": "image/png"},
    )
    ident = Identifier(type=IdentifierType.AVATAR, value=AVATAR_URL)
    async with httpx.AsyncClient() as client:
        collector = AvatarCollector(client=client)
        assert await collector.collect(ident) == []


async def test_collect_skips_oversize_bodies(httpx_mock: HTTPXMock) -> None:
    """Bodies larger than the cap silently no-op so memory stays bounded."""
    body = _png_bytes()
    httpx_mock.add_response(
        url=AVATAR_URL,
        method="GET",
        content=body,
        headers={"content-type": "image/png"},
    )
    ident = Identifier(type=IdentifierType.AVATAR, value=AVATAR_URL)
    async with httpx.AsyncClient() as client:
        collector = AvatarCollector(client=client, max_bytes=len(body) - 1)
        assert await collector.collect(ident) == []


async def test_collect_handles_palette_mode_png(httpx_mock: HTTPXMock) -> None:
    """Palette-mode (P) GIFs / PNGs should be hashed via their decoded RGB form.

    Without the convert-to-RGB shim, ``imagehash`` raises on ``mode == 'P'``.
    """
    img = Image.new("P", (32, 32))
    img.putpalette([0, 0, 0] * 256)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    body = buf.getvalue()

    httpx_mock.add_response(
        url=AVATAR_URL,
        method="GET",
        content=body,
        headers={"content-type": "image/png"},
    )
    ident = Identifier(type=IdentifierType.AVATAR, value=AVATAR_URL)
    async with httpx.AsyncClient() as client:
        collector = AvatarCollector(client=client)
        traces = await collector.collect(ident)
    assert len(traces) == 1
    fields = traces[0].fields
    # Mode comes from the raw image (Pillow loads it as P even after we
    # shim the hashing); width/height + the three hashes are still set.
    assert fields["mode"] == "P"
    assert len(fields["avatar_phash"]) == 16


async def test_collect_strips_content_type_parameters(httpx_mock: HTTPXMock) -> None:
    """``image/png; charset=binary`` must still be accepted as image/png."""
    body = _png_bytes()
    httpx_mock.add_response(
        url=AVATAR_URL,
        method="GET",
        content=body,
        headers={"content-type": "image/png; charset=binary"},
    )
    ident = Identifier(type=IdentifierType.AVATAR, value=AVATAR_URL)
    async with httpx.AsyncClient() as client:
        collector = AvatarCollector(client=client)
        traces = await collector.collect(ident)
    assert len(traces) == 1
    assert traces[0].fields["content_type"] == "image/png"


async def test_collect_sends_user_agent_and_accept_headers(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=AVATAR_URL,
        method="GET",
        content=_png_bytes(),
        headers={"content-type": "image/png"},
        match_headers={
            "User-Agent": "Reckora/0.1",
            "Accept": "image/*",
        },
    )
    ident = Identifier(type=IdentifierType.AVATAR, value=AVATAR_URL)
    async with httpx.AsyncClient() as client:
        collector = AvatarCollector(client=client)
        traces = await collector.collect(ident)
    assert len(traces) == 1


async def test_default_max_bytes_is_five_mib() -> None:
    assert DEFAULT_MAX_BYTES == 5 * 1024 * 1024


async def test_supports_only_avatar_identifier(collector: AvatarCollector) -> None:
    assert collector.supports(Identifier(type=IdentifierType.AVATAR, value=AVATAR_URL))
    assert not collector.supports(Identifier(type=IdentifierType.WALLET, value="0x0"))
    assert not collector.supports(Identifier(type=IdentifierType.USERNAME, value="alice"))
