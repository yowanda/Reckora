"""Tests for the SocialPresenceProbeCollector.

The collector hits four auth-walled platforms (Instagram, Threads,
LinkedIn, Facebook) in parallel and emits one trace per platform with
a ``presence_status`` flag describing what (if anything) it could
verify. Each platform has different observable signals, so we mock the
full matrix here.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.social_presence import (
    FACEBOOK_PROFILE_BASE,
    INSTAGRAM_API_BASE,
    INSTAGRAM_PROFILE_BASE,
    LINKEDIN_PROFILE_BASE,
    THREADS_PROFILE_BASE,
    SocialPresenceProbeCollector,
)
from reckora.models.entity import Identifier, Trace
from reckora.models.enums import IdentifierType, TraceSource


@pytest.fixture
def user_alice() -> Identifier:
    return Identifier(type=IdentifierType.USERNAME, value="alice")


def _ig_api(handle: str) -> str:
    return f"{INSTAGRAM_API_BASE}/api/v1/users/web_profile_info/?username={handle}"


def _ig_profile(handle: str) -> str:
    return f"{INSTAGRAM_PROFILE_BASE}/{handle}/"


def _threads(handle: str) -> str:
    return f"{THREADS_PROFILE_BASE}/@{handle}"


def _linkedin(handle: str) -> str:
    return f"{LINKEDIN_PROFILE_BASE}/in/{handle}/"


def _facebook(handle: str) -> str:
    return f"{FACEBOOK_PROFILE_BASE}/{handle}"


def _by_platform(traces: list[Trace]) -> dict[str, dict[str, Any]]:
    return {trace.fields["platform"]: trace.fields for trace in traces}


def _stub_threads(httpx_mock: HTTPXMock, handle: str, *, status: int = 200) -> None:
    httpx_mock.add_response(
        url=_threads(handle), status_code=status, text="<html><title>Threads</title></html>"
    )


def _stub_facebook_login_wall(httpx_mock: HTTPXMock, handle: str) -> None:
    """Facebook always redirects to /login/?next=...; pytest-httpx
    needs the redirect target stubbed explicitly because httpx follows
    redirects on its side, not pytest-httpx's.
    """
    httpx_mock.add_response(
        url=_facebook(handle),
        status_code=302,
        headers={"location": f"{FACEBOOK_PROFILE_BASE}/login/?next=/{handle}"},
    )
    httpx_mock.add_response(
        url=f"{FACEBOOK_PROFILE_BASE}/login/?next=/{handle}",
        status_code=200,
        text="<html><title>Log in to Facebook</title></html>",
    )


# --- supports() / pre-flight ---------------------------------------------


def test_supports_only_username_identifier() -> None:
    collector = SocialPresenceProbeCollector()
    assert collector.supports(Identifier(type=IdentifierType.USERNAME, value="a"))
    assert not collector.supports(Identifier(type=IdentifierType.EMAIL, value="a@b.co"))


async def test_collect_skips_unsupported_identifier() -> None:
    collector = SocialPresenceProbeCollector()
    ident = Identifier(type=IdentifierType.EMAIL, value="alice@example.com")
    assert await collector.collect(ident) == []


async def test_collect_rejects_invalid_handle_without_network() -> None:
    collector = SocialPresenceProbeCollector()
    bad = Identifier(type=IdentifierType.USERNAME, value="alice space")
    assert await collector.collect(bad) == []


# --- happy mixed path ----------------------------------------------------


async def test_collect_emits_one_trace_per_platform(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """A clean run: IG verified, Threads unverified, LinkedIn verified,
    Facebook unverified. Each platform must produce exactly one trace."""
    httpx_mock.add_response(
        url=_ig_api("alice"),
        status_code=200,
        json={
            "data": {
                "user": {
                    "pk": "12345",
                    "username": "alice",
                    "full_name": "Alice A",
                    "is_private": False,
                    "is_verified": True,
                    "biography": "OSINT analyst.",
                    "edge_followed_by": {"count": 10_000},
                }
            }
        },
    )
    _stub_threads(httpx_mock, "alice")
    httpx_mock.add_response(
        url=_linkedin("alice"),
        status_code=200,
        text="<html><head><title>Alice A - Founder | LinkedIn</title></head></html>",
    )
    _stub_facebook_login_wall(httpx_mock, "alice")

    async with httpx.AsyncClient() as client:
        collector = SocialPresenceProbeCollector(client=client)
        traces = await collector.collect(user_alice)

    by_platform = _by_platform(traces)
    assert set(by_platform) == {"instagram", "threads", "linkedin", "facebook"}
    for trace in traces:
        assert trace.source == TraceSource.SOCIAL_PRESENCE_PROBE

    ig = by_platform["instagram"]
    assert ig["presence_status"] == "exists"
    assert ig["http_status"] == 200
    assert ig["profile_url"] == "https://www.instagram.com/alice/"
    assert ig["display_name"] == "Alice A"
    assert ig["is_verified"] is True
    assert ig["follower_count"] == 10_000

    th = by_platform["threads"]
    assert th["presence_status"] == "unverified"
    assert th["profile_url"] == "https://www.threads.net/@alice"

    li = by_platform["linkedin"]
    assert li["presence_status"] == "exists"
    assert li["profile_url"] == "https://www.linkedin.com/in/alice/"
    assert li["display_name"] == "Alice A"

    fb = by_platform["facebook"]
    assert fb["presence_status"] == "unverified"
    assert fb["profile_url"] == "https://www.facebook.com/alice"


# --- per-platform detail ---------------------------------------------------


async def test_instagram_404_marks_not_found(httpx_mock: HTTPXMock, user_alice: Identifier) -> None:
    httpx_mock.add_response(url=_ig_api("alice"), status_code=404)
    _stub_threads(httpx_mock, "alice")
    httpx_mock.add_response(url=_linkedin("alice"), status_code=999)
    _stub_facebook_login_wall(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = SocialPresenceProbeCollector(client=client)
        by_platform = _by_platform(await collector.collect(user_alice))
    assert by_platform["instagram"]["presence_status"] == "not_found"
    assert by_platform["instagram"]["http_status"] == 404


async def test_instagram_429_marks_blocked(httpx_mock: HTTPXMock, user_alice: Identifier) -> None:
    httpx_mock.add_response(url=_ig_api("alice"), status_code=429)
    _stub_threads(httpx_mock, "alice")
    httpx_mock.add_response(url=_linkedin("alice"), status_code=999)
    _stub_facebook_login_wall(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = SocialPresenceProbeCollector(client=client)
        by_platform = _by_platform(await collector.collect(user_alice))
    assert by_platform["instagram"]["presence_status"] == "blocked"
    assert by_platform["instagram"]["http_status"] == 429


async def test_instagram_401_marks_blocked(httpx_mock: HTTPXMock, user_alice: Identifier) -> None:
    httpx_mock.add_response(
        url=_ig_api("alice"),
        status_code=401,
        json={"message": "Please wait a few minutes", "require_login": True, "status": "fail"},
    )
    _stub_threads(httpx_mock, "alice")
    httpx_mock.add_response(url=_linkedin("alice"), status_code=999)
    _stub_facebook_login_wall(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = SocialPresenceProbeCollector(client=client)
        by_platform = _by_platform(await collector.collect(user_alice))
    assert by_platform["instagram"]["presence_status"] == "blocked"


async def test_instagram_200_with_null_user_marks_not_found(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """Instagram returns ``{"data": {"user": null}}`` for some takedowns."""
    httpx_mock.add_response(
        url=_ig_api("alice"),
        status_code=200,
        json={"data": {"user": None}},
    )
    _stub_threads(httpx_mock, "alice")
    httpx_mock.add_response(url=_linkedin("alice"), status_code=999)
    _stub_facebook_login_wall(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = SocialPresenceProbeCollector(client=client)
        by_platform = _by_platform(await collector.collect(user_alice))
    assert by_platform["instagram"]["presence_status"] == "not_found"


async def test_threads_404_marks_not_found(httpx_mock: HTTPXMock, user_alice: Identifier) -> None:
    httpx_mock.add_response(url=_ig_api("alice"), status_code=429)
    _stub_threads(httpx_mock, "alice", status=404)
    httpx_mock.add_response(url=_linkedin("alice"), status_code=999)
    _stub_facebook_login_wall(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = SocialPresenceProbeCollector(client=client)
        by_platform = _by_platform(await collector.collect(user_alice))
    assert by_platform["threads"]["presence_status"] == "not_found"


async def test_linkedin_999_marks_blocked(httpx_mock: HTTPXMock, user_alice: Identifier) -> None:
    httpx_mock.add_response(url=_ig_api("alice"), status_code=429)
    _stub_threads(httpx_mock, "alice")
    httpx_mock.add_response(url=_linkedin("alice"), status_code=999)
    _stub_facebook_login_wall(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = SocialPresenceProbeCollector(client=client)
        by_platform = _by_platform(await collector.collect(user_alice))
    assert by_platform["linkedin"]["presence_status"] == "blocked"
    assert by_platform["linkedin"]["http_status"] == 999


async def test_linkedin_404_marks_not_found(httpx_mock: HTTPXMock, user_alice: Identifier) -> None:
    httpx_mock.add_response(url=_ig_api("alice"), status_code=429)
    _stub_threads(httpx_mock, "alice")
    httpx_mock.add_response(url=_linkedin("alice"), status_code=404)
    _stub_facebook_login_wall(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = SocialPresenceProbeCollector(client=client)
        by_platform = _by_platform(await collector.collect(user_alice))
    assert by_platform["linkedin"]["presence_status"] == "not_found"


async def test_linkedin_404_via_title_marks_not_found(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """LinkedIn sometimes serves a 200 page whose <title> says
    "Page not found"; treat that as ``not_found`` rather than ``exists``."""
    httpx_mock.add_response(url=_ig_api("alice"), status_code=429)
    _stub_threads(httpx_mock, "alice")
    httpx_mock.add_response(
        url=_linkedin("alice"),
        status_code=200,
        text="<html><head><title>Page not found | LinkedIn</title></head></html>",
    )
    _stub_facebook_login_wall(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = SocialPresenceProbeCollector(client=client)
        by_platform = _by_platform(await collector.collect(user_alice))
    assert by_platform["linkedin"]["presence_status"] == "not_found"


async def test_facebook_404_marks_not_found(httpx_mock: HTTPXMock, user_alice: Identifier) -> None:
    httpx_mock.add_response(url=_ig_api("alice"), status_code=429)
    _stub_threads(httpx_mock, "alice")
    httpx_mock.add_response(url=_linkedin("alice"), status_code=999)
    httpx_mock.add_response(url=_facebook("alice"), status_code=404)
    async with httpx.AsyncClient() as client:
        collector = SocialPresenceProbeCollector(client=client)
        by_platform = _by_platform(await collector.collect(user_alice))
    assert by_platform["facebook"]["presence_status"] == "not_found"


async def test_collect_strips_at_prefix(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_ig_api("alice"), status_code=429)
    _stub_threads(httpx_mock, "alice")
    httpx_mock.add_response(url=_linkedin("alice"), status_code=999)
    _stub_facebook_login_wall(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = SocialPresenceProbeCollector(client=client)
        ident = Identifier(type=IdentifierType.USERNAME, value="@alice")
        by_platform = _by_platform(await collector.collect(ident))
    # ``@alice`` ⇒ canonical handle ``alice`` echoed back through every
    # platform's profile URL builder.
    assert by_platform["threads"]["profile_url"] == "https://www.threads.net/@alice"


async def test_collect_skips_blank_handle() -> None:
    collector = SocialPresenceProbeCollector()
    ident = Identifier(type=IdentifierType.USERNAME, value="   ")
    assert await collector.collect(ident) == []
