"""Tests for the TikTok web rehydration collector."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.tiktok import TIKTOK_PROFILE_BASE, TikTokCollector
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource


@pytest.fixture
def user_tiktok() -> Identifier:
    return Identifier(type=IdentifierType.USERNAME, value="tiktok")


def _tiktok_url(handle: str) -> str:
    return f"{TIKTOK_PROFILE_BASE}/@{handle}"


def _wrap_rehydration(data: dict[str, Any]) -> str:
    return (
        "<html><body>"
        '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        f"{json.dumps(data)}"
        "</script></body></html>"
    )


def _existing_payload() -> dict[str, Any]:
    return {
        "__DEFAULT_SCOPE__": {
            "webapp.user-detail": {
                "statusCode": 0,
                "userInfo": {
                    "user": {
                        "id": "107955",
                        "uniqueId": "tiktok",
                        "secUid": "MS4wLjABAAAAv7iSuuXDJG",
                        "nickname": "TikTok",
                        "signature": "One TikTok can make a big impact",
                        "verified": True,
                        "privateAccount": False,
                        "region": "US",
                        "createTime": 1_425_144_149,
                        "avatarLarger": (
                            "https://p19-common-sign.tiktokcdn-us.com/tos/avatar.jpeg?dr=9640&t=4d"
                        ),
                    },
                    "stats": {
                        "followerCount": 94_000_000,
                        "followingCount": 3,
                        "videoCount": 1_431,
                        "heartCount": 458_000_000,
                    },
                },
            }
        }
    }


# --- supports() / pre-flight ---------------------------------------------


def test_supports_only_username_identifier() -> None:
    collector = TikTokCollector()
    assert collector.supports(Identifier(type=IdentifierType.USERNAME, value="a"))
    assert not collector.supports(Identifier(type=IdentifierType.EMAIL, value="a@b.co"))


async def test_collect_skips_unsupported_identifier() -> None:
    collector = TikTokCollector()
    ident = Identifier(type=IdentifierType.EMAIL, value="alice@example.com")
    assert await collector.collect(ident) == []


async def test_collect_rejects_too_long_handle_without_network() -> None:
    collector = TikTokCollector()
    too_long = Identifier(type=IdentifierType.USERNAME, value="x" * 25)
    assert await collector.collect(too_long) == []


async def test_collect_strips_at_prefix(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_tiktok_url("tiktok"),
        text=_wrap_rehydration(_existing_payload()),
    )
    async with httpx.AsyncClient() as client:
        collector = TikTokCollector(client=client)
        ident = Identifier(type=IdentifierType.USERNAME, value="@tiktok")
        traces = await collector.collect(ident)
    assert len(traces) == 1


# --- happy path -----------------------------------------------------------


async def test_collect_normalises_full_profile(
    httpx_mock: HTTPXMock, user_tiktok: Identifier
) -> None:
    httpx_mock.add_response(
        url=_tiktok_url("tiktok"),
        text=_wrap_rehydration(_existing_payload()),
    )
    async with httpx.AsyncClient() as client:
        collector = TikTokCollector(client=client)
        traces = await collector.collect(user_tiktok)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.source == TraceSource.TIKTOK_WEB
    fields = trace.fields
    assert fields["platform"] == "tiktok"
    assert fields["profile_url"] == "https://www.tiktok.com/@tiktok"
    assert fields["unique_id"] == "tiktok"
    assert fields["sec_uid"] == "MS4wLjABAAAAv7iSuuXDJG"
    assert fields["user_id"] == "107955"
    assert fields["display_name"] == "TikTok"
    assert fields["bio"] == "One TikTok can make a big impact"
    # querystring stripped
    assert fields["avatar_url"] == "https://p19-common-sign.tiktokcdn-us.com/tos/avatar.jpeg"
    assert fields["verified"] is True
    assert fields["private_account"] is False
    assert fields["region"] == "US"
    assert fields["created_at"] == "2015-02-28T17:22:29+00:00"
    assert fields["followers_count"] == 94_000_000
    assert fields["following_count"] == 3
    assert fields["video_count"] == 1_431
    assert fields["heart_count"] == 458_000_000


# --- miss / error paths ---------------------------------------------------


async def test_collect_returns_empty_for_status_code_10221(
    httpx_mock: HTTPXMock, user_tiktok: Identifier
) -> None:
    """``statusCode: 10221`` is TikTok's "user not found"."""
    payload: dict[str, Any] = {
        "__DEFAULT_SCOPE__": {
            "webapp.user-detail": {"statusCode": 10221, "statusMsg": "user not found"}
        }
    }
    httpx_mock.add_response(url=_tiktok_url("tiktok"), text=_wrap_rehydration(payload))
    async with httpx.AsyncClient() as client:
        collector = TikTokCollector(client=client)
        assert await collector.collect(user_tiktok) == []


async def test_collect_404_returns_empty(httpx_mock: HTTPXMock, user_tiktok: Identifier) -> None:
    httpx_mock.add_response(url=_tiktok_url("tiktok"), status_code=404)
    async with httpx.AsyncClient() as client:
        collector = TikTokCollector(client=client)
        assert await collector.collect(user_tiktok) == []


async def test_collect_403_propagates(httpx_mock: HTTPXMock, user_tiktok: Identifier) -> None:
    """403 = TikTok rate-limited us; surface so the orchestrator can log."""
    httpx_mock.add_response(url=_tiktok_url("tiktok"), status_code=403)
    async with httpx.AsyncClient() as client:
        collector = TikTokCollector(client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await collector.collect(user_tiktok)


async def test_collect_handles_missing_rehydration_script(
    httpx_mock: HTTPXMock, user_tiktok: Identifier
) -> None:
    httpx_mock.add_response(
        url=_tiktok_url("tiktok"), text="<html><body>no rehydration</body></html>"
    )
    async with httpx.AsyncClient() as client:
        collector = TikTokCollector(client=client)
        assert await collector.collect(user_tiktok) == []


async def test_collect_handles_malformed_rehydration_json(
    httpx_mock: HTTPXMock, user_tiktok: Identifier
) -> None:
    httpx_mock.add_response(
        url=_tiktok_url("tiktok"),
        text='<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">{bad json</script>',
    )
    async with httpx.AsyncClient() as client:
        collector = TikTokCollector(client=client)
        assert await collector.collect(user_tiktok) == []


async def test_collect_returns_empty_when_user_payload_missing_unique_id(
    httpx_mock: HTTPXMock, user_tiktok: Identifier
) -> None:
    payload: dict[str, Any] = {
        "__DEFAULT_SCOPE__": {
            "webapp.user-detail": {
                "statusCode": 0,
                "userInfo": {"user": {}, "stats": {}},
            }
        }
    }
    httpx_mock.add_response(url=_tiktok_url("tiktok"), text=_wrap_rehydration(payload))
    async with httpx.AsyncClient() as client:
        collector = TikTokCollector(client=client)
        assert await collector.collect(user_tiktok) == []


async def test_collect_skips_blank_handle() -> None:
    collector = TikTokCollector()
    ident = Identifier(type=IdentifierType.USERNAME, value="   ")
    assert await collector.collect(ident) == []
