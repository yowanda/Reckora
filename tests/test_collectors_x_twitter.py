"""Tests for the X / Twitter syndication collector.

The collector hits ``syndication.twitter.com/srv/timeline-profile/
screen-name/<u>`` and parses the embedded ``__NEXT_DATA__`` blob. We
mock the HTTP layer with ``pytest-httpx`` and stub the blob with the
minimal slice of X's wire shape we depend on.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.x_twitter import X_SYNDICATION_BASE, XCollector
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource


@pytest.fixture
def user_jack() -> Identifier:
    return Identifier(type=IdentifierType.USERNAME, value="jack")


def _x_url(handle: str) -> str:
    return f"{X_SYNDICATION_BASE}/srv/timeline-profile/screen-name/{handle}?showReplies=false"


def _wrap_next_data(data: dict[str, object]) -> str:
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'


def _full_user_payload() -> dict[str, object]:
    """Mirror the slice of X's profile widget JSON we depend on."""
    return {
        "props": {
            "pageProps": {
                "headerProps": {"screenName": "jack"},
                "timeline": {
                    "entries": [
                        {
                            "type": "tweet",
                            "content": {
                                "tweet": {
                                    "id_str": "100",
                                    "user": {
                                        "id_str": "12",
                                        "screen_name": "jack",
                                        "name": "jack",
                                        "description": "no state is the best state",
                                        "followers_count": 7_363_263,
                                        "friends_count": 3,
                                        "statuses_count": 30_498,
                                        "created_at": "Tue Mar 21 20:50:14 +0000 2006",
                                        "profile_image_url_https": (
                                            "https://pbs.twimg.com/profile_images/1.jpg?cb=1"
                                        ),
                                        "profile_banner_url": (
                                            "https://pbs.twimg.com/profile_banners/12/2.png?cb=1"
                                        ),
                                        "verified": False,
                                        "location": "California",
                                        "url": "https://t.co/abc",
                                        "entities": {
                                            "url": {
                                                "urls": [
                                                    {
                                                        "url": "https://t.co/abc",
                                                        "expanded_url": "https://block.xyz",
                                                    },
                                                ],
                                            },
                                        },
                                    },
                                },
                            },
                        }
                    ]
                },
            }
        }
    }


# --- supports() ------------------------------------------------------------


def test_supports_only_username_identifier() -> None:
    collector = XCollector()
    assert collector.supports(Identifier(type=IdentifierType.USERNAME, value="a"))
    assert not collector.supports(Identifier(type=IdentifierType.EMAIL, value="a@b.co"))


async def test_collect_skips_unsupported_identifier() -> None:
    collector = XCollector()
    ident = Identifier(type=IdentifierType.EMAIL, value="alice@example.com")
    assert await collector.collect(ident) == []


async def test_collect_rejects_invalid_handle_without_network() -> None:
    """Handles longer than 15 chars or non-alphanumeric must short-circuit."""
    collector = XCollector()
    too_long = Identifier(type=IdentifierType.USERNAME, value="a" * 16)
    assert await collector.collect(too_long) == []
    has_dot = Identifier(type=IdentifierType.USERNAME, value="user.name")
    assert await collector.collect(has_dot) == []


async def test_collect_strips_at_prefix(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_x_url("jack"),
        text=_wrap_next_data(_full_user_payload()),
    )
    async with httpx.AsyncClient() as client:
        collector = XCollector(client=client)
        ident = Identifier(type=IdentifierType.USERNAME, value="@jack")
        traces = await collector.collect(ident)
    assert len(traces) == 1
    assert traces[0].fields["screen_name"] == "jack"


# --- happy path -----------------------------------------------------------


async def test_collect_normalises_full_profile(
    httpx_mock: HTTPXMock, user_jack: Identifier
) -> None:
    httpx_mock.add_response(
        url=_x_url("jack"),
        text=_wrap_next_data(_full_user_payload()),
    )
    async with httpx.AsyncClient() as client:
        collector = XCollector(client=client)
        traces = await collector.collect(user_jack)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.source == TraceSource.X_SYNDICATION
    fields = trace.fields
    assert fields["platform"] == "x"
    assert fields["profile_url"] == "https://x.com/jack"
    assert fields["screen_name"] == "jack"
    assert fields["user_id"] == "12"
    assert fields["display_name"] == "jack"
    assert fields["bio"] == "no state is the best state"
    # querystring stripped from CDN URL
    assert fields["avatar_url"] == "https://pbs.twimg.com/profile_images/1.jpg"
    assert fields["banner_url"] == "https://pbs.twimg.com/profile_banners/12/2.png"
    assert fields["followers_count"] == 7_363_263
    assert fields["friends_count"] == 3
    assert fields["statuses_count"] == 30_498
    # ``created_at`` parsed to ISO 8601
    assert fields["created_at"] == "2006-03-21T20:50:14+00:00"
    assert fields["verified"] is False
    assert fields["location"] == "California"
    # expanded URL preferred over t.co shortlink
    assert fields["url"] == "https://block.xyz"


async def test_collect_emits_header_only_when_timeline_empty(
    httpx_mock: HTTPXMock, user_jack: Identifier
) -> None:
    """Account exists but has zero tweets — surface a header-only trace."""
    payload = {
        "props": {
            "pageProps": {
                "headerProps": {"screenName": "jack"},
                "timeline": {"entries": []},
            }
        }
    }
    httpx_mock.add_response(url=_x_url("jack"), text=_wrap_next_data(payload))
    async with httpx.AsyncClient() as client:
        collector = XCollector(client=client)
        traces = await collector.collect(user_jack)

    assert len(traces) == 1
    fields = traces[0].fields
    assert fields["screen_name"] == "jack"
    assert fields["profile_url"] == "https://x.com/jack"
    # No tweet ⇒ no embedded user record ⇒ tombstone fields are None.
    assert fields["display_name"] is None
    assert fields["followers_count"] is None
    assert fields["created_at"] is None
    assert fields["verified"] is False


async def test_collect_falls_back_to_user_url_when_no_entities(
    httpx_mock: HTTPXMock, user_jack: Identifier
) -> None:
    payload = _full_user_payload()
    user = payload["props"]["pageProps"]["timeline"]["entries"][0]["content"]["tweet"][  # type: ignore[index]
        "user"
    ]
    user.pop("entities")  # type: ignore[attr-defined]
    httpx_mock.add_response(url=_x_url("jack"), text=_wrap_next_data(payload))
    async with httpx.AsyncClient() as client:
        collector = XCollector(client=client)
        traces = await collector.collect(user_jack)
    assert traces[0].fields["url"] == "https://t.co/abc"


# --- miss / error paths ---------------------------------------------------


async def test_collect_returns_empty_when_no_header_props(
    httpx_mock: HTTPXMock, user_jack: Identifier
) -> None:
    """Unknown user: widget renders without ``headerProps.screenName``."""
    payload = {"props": {"pageProps": {"timeline": {"entries": []}}}}
    httpx_mock.add_response(url=_x_url("jack"), text=_wrap_next_data(payload))
    async with httpx.AsyncClient() as client:
        collector = XCollector(client=client)
        assert await collector.collect(user_jack) == []


async def test_collect_404_returns_empty(httpx_mock: HTTPXMock, user_jack: Identifier) -> None:
    httpx_mock.add_response(url=_x_url("jack"), status_code=404)
    async with httpx.AsyncClient() as client:
        collector = XCollector(client=client)
        assert await collector.collect(user_jack) == []


async def test_collect_500_propagates(httpx_mock: HTTPXMock, user_jack: Identifier) -> None:
    httpx_mock.add_response(url=_x_url("jack"), status_code=500)
    async with httpx.AsyncClient() as client:
        collector = XCollector(client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await collector.collect(user_jack)


async def test_collect_handles_missing_next_data_script(
    httpx_mock: HTTPXMock, user_jack: Identifier
) -> None:
    httpx_mock.add_response(url=_x_url("jack"), text="<html><body>no script here</body></html>")
    async with httpx.AsyncClient() as client:
        collector = XCollector(client=client)
        assert await collector.collect(user_jack) == []


async def test_collect_handles_malformed_json_in_next_data(
    httpx_mock: HTTPXMock, user_jack: Identifier
) -> None:
    httpx_mock.add_response(
        url=_x_url("jack"),
        text='<script id="__NEXT_DATA__">{not valid json</script>',
    )
    async with httpx.AsyncClient() as client:
        collector = XCollector(client=client)
        assert await collector.collect(user_jack) == []


async def test_collect_skips_blank_handle() -> None:
    collector = XCollector()
    ident = Identifier(type=IdentifierType.USERNAME, value="   ")
    assert await collector.collect(ident) == []
