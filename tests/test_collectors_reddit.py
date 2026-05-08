"""Tests for the Reddit user-profile collector.

Reddit ships ``/user/{name}/about.json`` un-authenticated; we mock the
HTTP layer with ``pytest-httpx`` so the suite is hermetic and the
fixtures encode the wire shape we depend on.
"""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.reddit import REDDIT_API_BASE, RedditCollector
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource


@pytest.fixture
def user_alice() -> Identifier:
    return Identifier(type=IdentifierType.USERNAME, value="alice")


def _reddit_url(name: str) -> str:
    return f"{REDDIT_API_BASE}/user/{name}/about.json"


# --- supports() ------------------------------------------------------------


def test_supports_only_username_identifier() -> None:
    collector = RedditCollector()
    assert collector.supports(Identifier(type=IdentifierType.USERNAME, value="a"))
    assert not collector.supports(Identifier(type=IdentifierType.EMAIL, value="a@b.co"))
    assert not collector.supports(Identifier(type=IdentifierType.PHONE, value="+1"))


async def test_collect_skips_unsupported_identifier() -> None:
    collector = RedditCollector()
    ident = Identifier(type=IdentifierType.EMAIL, value="alice@example.com")
    assert await collector.collect(ident) == []


async def test_collect_skips_blank_username() -> None:
    """Whitespace-only seeds shouldn't issue a network call."""
    collector = RedditCollector()
    ident = Identifier(type=IdentifierType.USERNAME, value="   ")
    assert await collector.collect(ident) == []


# --- happy path -----------------------------------------------------------


async def test_collect_normalises_full_profile(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """A live account: every high-signal field surfaces in the trace."""
    httpx_mock.add_response(
        url=_reddit_url("alice"),
        json={
            "kind": "t2",
            "data": {
                "id": "abcd1",
                "name": "alice",
                "link_karma": 12_345,
                "comment_karma": 6_789,
                "total_karma": 19_134,
                "created_utc": 1_234_567_890,
                "has_verified_email": True,
                "is_employee": False,
                "is_gold": True,
                "is_mod": False,
                "is_suspended": False,
                "icon_img": "https://styles.reddit.com/avatar/abcd1.png?width=256&v=2",
                "subreddit": {
                    "title": "Alice in OSINT",
                    "public_description": "OSINT analyst.",
                    "icon_img": "https://styles.reddit.com/icon/abcd1.png?width=256",
                    "banner_img": "",
                },
            },
        },
    )
    async with httpx.AsyncClient() as client:
        collector = RedditCollector(client=client)
        traces = await collector.collect(user_alice)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.source == TraceSource.REDDIT_PROFILE
    fields = trace.fields
    assert fields["platform"] == "reddit"
    assert fields["profile_url"] == "https://www.reddit.com/user/alice/"
    assert fields["display_name"] == "Alice in OSINT"
    assert fields["bio"] == "OSINT analyst."
    # Querystring stripped so the URL is stable across runs.
    assert fields["avatar_url"] == "https://styles.reddit.com/avatar/abcd1.png"
    assert fields["link_karma"] == 12_345
    assert fields["comment_karma"] == 6_789
    assert fields["total_karma"] == 19_134
    assert fields["created_utc"] == "2009-02-13T23:31:30+00:00"
    assert fields["has_verified_email"] is True
    assert fields["is_employee"] is False
    assert fields["is_gold"] is True
    assert fields["is_mod"] is False
    assert fields["is_suspended"] is False


async def test_collect_falls_back_to_subreddit_icon_when_no_user_avatar(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    httpx_mock.add_response(
        url=_reddit_url("alice"),
        json={
            "data": {
                "name": "alice",
                "link_karma": 1,
                "comment_karma": 0,
                "total_karma": 1,
                "created_utc": 0,
                "icon_img": "",
                "subreddit": {
                    "icon_img": "https://styles.reddit.com/sr/abcd1.png?cb=1",
                    "banner_img": "",
                },
            }
        },
    )
    async with httpx.AsyncClient() as client:
        collector = RedditCollector(client=client)
        traces = await collector.collect(user_alice)

    assert traces[0].fields["avatar_url"] == "https://styles.reddit.com/sr/abcd1.png"


async def test_collect_falls_back_to_subreddit_banner_when_no_icons(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    httpx_mock.add_response(
        url=_reddit_url("alice"),
        json={
            "data": {
                "name": "alice",
                "link_karma": 0,
                "comment_karma": 0,
                "total_karma": 0,
                "created_utc": 0,
                "icon_img": "",
                "subreddit": {
                    "icon_img": "",
                    "banner_img": "https://styles.reddit.com/banner/abcd1.png?cb=2",
                },
            }
        },
    )
    async with httpx.AsyncClient() as client:
        collector = RedditCollector(client=client)
        traces = await collector.collect(user_alice)

    assert traces[0].fields["avatar_url"] == "https://styles.reddit.com/banner/abcd1.png"


async def test_collect_returns_no_avatar_when_all_fields_missing(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    httpx_mock.add_response(
        url=_reddit_url("alice"),
        json={
            "data": {
                "name": "alice",
                "link_karma": 0,
                "comment_karma": 0,
                "total_karma": 0,
                "created_utc": 0,
                "subreddit": {},
            }
        },
    )
    async with httpx.AsyncClient() as client:
        collector = RedditCollector(client=client)
        traces = await collector.collect(user_alice)

    assert traces[0].fields["avatar_url"] is None


# --- error / suspension paths --------------------------------------------


async def test_collect_404_returns_empty_list(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """Unknown account: no trace, just an empty list."""
    httpx_mock.add_response(url=_reddit_url("alice"), status_code=404)
    async with httpx.AsyncClient() as client:
        collector = RedditCollector(client=client)
        assert await collector.collect(user_alice) == []


async def test_collect_403_returns_empty_list(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """Reddit returns 403 for some shadow-banned / takedown'd accounts."""
    httpx_mock.add_response(url=_reddit_url("alice"), status_code=403)
    async with httpx.AsyncClient() as client:
        collector = RedditCollector(client=client)
        assert await collector.collect(user_alice) == []


async def test_collect_451_returns_empty_list(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """451 = Unavailable for Legal Reasons; treat like a takedown."""
    httpx_mock.add_response(url=_reddit_url("alice"), status_code=451)
    async with httpx.AsyncClient() as client:
        collector = RedditCollector(client=client)
        assert await collector.collect(user_alice) == []


async def test_collect_429_propagates(httpx_mock: HTTPXMock, user_alice: Identifier) -> None:
    """Rate-limit MUST surface so the orchestrator can back off / log."""
    httpx_mock.add_response(url=_reddit_url("alice"), status_code=429)
    async with httpx.AsyncClient() as client:
        collector = RedditCollector(client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await collector.collect(user_alice)


async def test_collect_500_propagates(httpx_mock: HTTPXMock, user_alice: Identifier) -> None:
    httpx_mock.add_response(url=_reddit_url("alice"), status_code=500)
    async with httpx.AsyncClient() as client:
        collector = RedditCollector(client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await collector.collect(user_alice)


async def test_collect_suspended_account_emits_terse_trace(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """Reddit returns ``{"is_suspended": true}`` and omits most fields."""
    httpx_mock.add_response(
        url=_reddit_url("alice"),
        json={"data": {"is_suspended": True, "name": "alice"}},
    )
    async with httpx.AsyncClient() as client:
        collector = RedditCollector(client=client)
        traces = await collector.collect(user_alice)

    assert len(traces) == 1
    fields = traces[0].fields
    assert fields["is_suspended"] is True
    assert fields["link_karma"] is None
    assert fields["comment_karma"] is None
    assert fields["display_name"] is None
    assert fields["avatar_url"] is None
    # The profile URL still resolves to something the dossier can link.
    assert fields["profile_url"] == "https://www.reddit.com/user/alice/"


async def test_collect_handles_non_json_body(httpx_mock: HTTPXMock, user_alice: Identifier) -> None:
    """A non-JSON 200 (Reddit serves HTML on rate-limit interstitials)
    must NOT explode the collector — treat as 'no profile'."""
    httpx_mock.add_response(
        url=_reddit_url("alice"),
        content=b"<html>nope</html>",
        headers={"Content-Type": "text/html"},
    )
    async with httpx.AsyncClient() as client:
        collector = RedditCollector(client=client)
        assert await collector.collect(user_alice) == []


async def test_collect_handles_envelope_without_data(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    httpx_mock.add_response(url=_reddit_url("alice"), json={"kind": "t2"})
    async with httpx.AsyncClient() as client:
        collector = RedditCollector(client=client)
        assert await collector.collect(user_alice) == []


# --- header contract ------------------------------------------------------


async def test_collect_sends_required_user_agent(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    httpx_mock.add_response(url=_reddit_url("alice"), status_code=404)
    async with httpx.AsyncClient() as client:
        collector = RedditCollector(client=client)
        await collector.collect(user_alice)

    request = httpx_mock.get_requests()[0]
    # Default UA mentions reckora so Reddit's rate-limit team can identify us.
    assert "reckora" in request.headers["User-Agent"].lower()
    assert request.headers["Accept"] == "application/json"


async def test_collect_respects_custom_user_agent(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    httpx_mock.add_response(url=_reddit_url("alice"), status_code=404)
    async with httpx.AsyncClient() as client:
        collector = RedditCollector(
            client=client,
            user_agent="custom-app/1.0 (by /u/me)",
        )
        await collector.collect(user_alice)

    request = httpx_mock.get_requests()[0]
    assert request.headers["User-Agent"] == "custom-app/1.0 (by /u/me)"


# --- evidence -------------------------------------------------------------


async def test_collect_evidence_drops_raw_payload(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """Raw response can be huge; only the SHA + URL survive in evidence."""
    httpx_mock.add_response(
        url=_reddit_url("alice"),
        json={
            "data": {
                "name": "alice",
                "link_karma": 1,
                "comment_karma": 1,
                "total_karma": 2,
                "created_utc": 0,
                "subreddit": {},
            }
        },
    )
    async with httpx.AsyncClient() as client:
        collector = RedditCollector(client=client)
        traces = await collector.collect(user_alice)

    evidence = traces[0].evidence
    assert evidence.raw_payload is None
    assert evidence.source_url == _reddit_url("alice")
    assert len(evidence.payload_sha256) == 64
