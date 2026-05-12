"""Tests for the LLM-backed web-search helper.

Two transport paths (Platform API key, ChatGPT OAuth) share the same
SSE-stream parsing code path. We stub the Responses API at the HTTP
layer with ``pytest-httpx`` and assert the helper extracts
``url_citation`` annotations correctly, raises :class:`WebSearchError`
on backend failures, and routes between the two transports based on
which credentials are available.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.auth.oauth import CHATGPT_CODEX_BASE_URL, OAuthCredentials
from reckora.reasoning.web_search import (
    DEFAULT_INSTRUCTIONS,
    OPENAI_RESPONSES_URL,
    WebSearchError,
    WebSearchHit,
    WebSearchRateLimitError,
    WebSearchUnavailableError,
    make_web_search_fn,
    web_search_via_chatgpt_oauth,
    web_search_via_platform_api,
)


def _sse(events: list[dict[str, Any]]) -> bytes:
    """Serialise events into an SSE response body (bytes for HTTPXMock)."""
    import json

    out_parts: list[str] = []
    for event in events:
        out_parts.append(f"data: {json.dumps(event)}")
        out_parts.append("")  # blank line between events
    out_parts.append("data: [DONE]")
    out_parts.append("")
    return ("\n".join(out_parts) + "\n").encode("utf-8")


_TWO_CITATIONS_SSE = _sse(
    [
        {
            "type": "response.output_item.done",
            "item": {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": (
                            "Found a leaked Alice doc on scribd and a slideshare deck about Alice."
                        ),
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://www.scribd.com/document/123456/Alice-Resume",
                                "title": "Alice resume PDF",
                                "start_index": 11,
                                "end_index": 30,
                            },
                            {
                                "type": "url_citation",
                                "url": "https://www.slideshare.net/alice/deck",
                                "title": "Alice deck",
                                "start_index": 45,
                                "end_index": 67,
                            },
                        ],
                    }
                ],
            },
        },
        {"type": "response.completed", "response": {"output": []}},
    ]
)


_EMPTY_CITATIONS_SSE = _sse(
    [
        {
            "type": "response.output_item.done",
            "item": {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "No public mentions found.",
                        "annotations": [],
                    }
                ],
            },
        },
        {"type": "response.completed", "response": {"output": []}},
    ]
)


_DUPLICATE_URL_SSE = _sse(
    [
        {
            "type": "response.output_item.done",
            "item": {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Same URL twice",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://issuu.com/alice/docs/notes",
                                "title": "Notes",
                                "start_index": 0,
                                "end_index": 4,
                            },
                            {
                                "type": "url_citation",
                                "url": "https://issuu.com/alice/docs/notes",
                                "title": "Notes (dup)",
                                "start_index": 5,
                                "end_index": 9,
                            },
                        ],
                    }
                ],
            },
        }
    ]
)


_NON_URL_ANNOTATION_SSE = _sse(
    [
        {
            "type": "response.output_item.done",
            "item": {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "file citation only",
                        "annotations": [
                            {"type": "file_citation", "file_id": "file-abc"},
                            {
                                "type": "url_citation",
                                "url": "https://docplayer.net/12345-report.html",
                                "title": "report",
                            },
                        ],
                    }
                ],
            },
        }
    ]
)


_ERROR_EVENT_SSE = _sse(
    [
        {"type": "error", "message": "model temporarily unavailable"},
    ]
)


# Mirrors what the Codex OAuth backend actually returns: the
# ``web_search`` tool runs to completion, then the model emits a final
# assistant message whose ``text`` block lists the result URLs on
# separate lines but whose ``annotations`` array is empty (Codex does
# NOT auto-emit ``url_citation`` records the way ``api.openai.com``'s
# ``web_search_preview`` does). The helper has to fall back to scraping
# the message text — see :func:`_extract_urls_from_text`.
_OAUTH_BARE_URLS_IN_TEXT_SSE = _sse(
    [
        {
            "type": "response.output_item.done",
            "item": {
                "type": "web_search_call",
                "status": "completed",
                "action": {"type": "search", "query": "site:scribd.com alice"},
            },
        },
        {
            "type": "response.output_item.done",
            "item": {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": (
                            "https://www.scribd.com/document/111/Alice-Resume\n\n"
                            "https://www.scribd.com/document/222/Alice-CV\n\n"
                            "Trailing prose with a duplicate "
                            "https://www.scribd.com/document/111/Alice-Resume and "
                            "an inline (https://issuu.com/alice/docs/notes)."
                        ),
                        "annotations": [],
                    }
                ],
            },
        },
        {"type": "response.completed", "response": {"output": []}},
    ]
)


# Per-platform endpoint helpers — both transports share the same body
# shape but post to different URLs.
_OAUTH_URL = f"{CHATGPT_CODEX_BASE_URL}/responses"


def _fresh_credentials() -> OAuthCredentials:
    """Return a not-yet-expired OAuthCredentials suitable for tests."""
    return OAuthCredentials(
        access_token="oauth-access-token",
        refresh_token="oauth-refresh-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


# ------------------------------------------------------- platform path tests


async def test_platform_api_parses_two_citations(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=OPENAI_RESPONSES_URL,
        method="POST",
        content=_TWO_CITATIONS_SSE,
        headers={"Content-Type": "text/event-stream"},
    )
    async with httpx.AsyncClient() as client:
        hits = await web_search_via_platform_api(
            'site:scribd.com "alice"',
            api_key="sk-test",
            client=client,
        )
    assert [h.url for h in hits] == [
        "https://www.scribd.com/document/123456/Alice-Resume",
        "https://www.slideshare.net/alice/deck",
    ]
    assert hits[0].title == "Alice resume PDF"
    # Snippet is sliced from the assistant text using start/end_index.
    assert hits[0].snippet


async def test_platform_api_request_authorisation_and_body(httpx_mock: HTTPXMock) -> None:
    """The Platform path sends Bearer + the documented tool type."""
    httpx_mock.add_response(
        url=OPENAI_RESPONSES_URL,
        method="POST",
        content=_EMPTY_CITATIONS_SSE,
        headers={"Content-Type": "text/event-stream"},
    )
    async with httpx.AsyncClient() as client:
        await web_search_via_platform_api(
            "alice",
            api_key="sk-secret",
            client=client,
            model="gpt-4o-mini",
        )
    request = httpx_mock.get_requests()[0]
    assert request.headers["Authorization"] == "Bearer sk-secret"

    import json

    body = json.loads(request.content)
    assert body["model"] == "gpt-4o-mini"
    assert body["tools"] == [{"type": "web_search_preview"}]
    assert body["tool_choice"] == {"type": "web_search_preview"}
    assert body["stream"] is True
    # ``instructions`` is mandatory on the Codex backend and harmless on
    # the Platform path — the helper threads the default through both so
    # the regression that produced ``HTTP 400 {"detail":"Instructions are
    # required"}`` against ``chatgpt.com/backend-api/codex/responses``
    # can't reappear under either transport.
    assert body["instructions"] == DEFAULT_INSTRUCTIONS


async def test_platform_api_4xx_raises_websearcherror(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=OPENAI_RESPONSES_URL,
        method="POST",
        status_code=429,
        text="rate limit",
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(WebSearchError) as exc_info:
            await web_search_via_platform_api(
                "alice",
                api_key="sk-test",
                client=client,
            )
    assert "429" in str(exc_info.value)


async def test_platform_api_error_event_raises_websearcherror(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=OPENAI_RESPONSES_URL,
        method="POST",
        content=_ERROR_EVENT_SSE,
        headers={"Content-Type": "text/event-stream"},
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(WebSearchError) as exc_info:
            await web_search_via_platform_api(
                "alice",
                api_key="sk-test",
                client=client,
            )
    assert "model temporarily unavailable" in str(exc_info.value)


# ------------------------------------------------------- OAuth path tests


async def test_oauth_request_routes_to_codex_endpoint_and_uses_short_tool_name(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=_OAUTH_URL,
        method="POST",
        content=_EMPTY_CITATIONS_SSE,
        headers={"Content-Type": "text/event-stream"},
    )
    creds = _fresh_credentials()
    async with httpx.AsyncClient() as client:
        await web_search_via_chatgpt_oauth(
            "alice",
            credentials=creds,
            client=client,
        )
    request = httpx_mock.get_requests()[0]
    assert request.headers["Authorization"] == f"Bearer {creds.access_token}"

    import json

    body = json.loads(request.content)
    # Codex backend uses the short ``web_search`` tool name.
    assert body["tools"] == [{"type": "web_search"}]
    assert body["tool_choice"] == {"type": "web_search"}
    # Codex backend rejects ``store=True``; the helper must send False.
    assert body["store"] is False
    # The Codex backend also rejects requests with no ``instructions``
    # (``HTTP 400 {"detail":"Instructions are required"}``); the helper
    # always sends :data:`DEFAULT_INSTRUCTIONS` so the OAuth path stops
    # silently 400-ing every doc-leak query.
    assert body["instructions"] == DEFAULT_INSTRUCTIONS


# ----------------------------------------------- citation parser invariants


async def test_duplicate_url_is_deduplicated(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=OPENAI_RESPONSES_URL,
        method="POST",
        content=_DUPLICATE_URL_SSE,
        headers={"Content-Type": "text/event-stream"},
    )
    async with httpx.AsyncClient() as client:
        hits = await web_search_via_platform_api(
            "alice",
            api_key="sk-test",
            client=client,
        )
    assert len(hits) == 1
    assert hits[0].url == "https://issuu.com/alice/docs/notes"


async def test_non_url_annotation_is_ignored(httpx_mock: HTTPXMock) -> None:
    """File citations / unknown annotation types must not pollute the hit list."""
    httpx_mock.add_response(
        url=OPENAI_RESPONSES_URL,
        method="POST",
        content=_NON_URL_ANNOTATION_SSE,
        headers={"Content-Type": "text/event-stream"},
    )
    async with httpx.AsyncClient() as client:
        hits = await web_search_via_platform_api(
            "alice",
            api_key="sk-test",
            client=client,
        )
    assert [h.url for h in hits] == ["https://docplayer.net/12345-report.html"]


async def test_limit_truncates_returned_hits(httpx_mock: HTTPXMock) -> None:
    """``limit=1`` keeps only the first citation."""
    httpx_mock.add_response(
        url=OPENAI_RESPONSES_URL,
        method="POST",
        content=_TWO_CITATIONS_SSE,
        headers={"Content-Type": "text/event-stream"},
    )
    async with httpx.AsyncClient() as client:
        hits = await web_search_via_platform_api(
            "alice",
            api_key="sk-test",
            client=client,
            limit=1,
        )
    assert len(hits) == 1
    assert hits[0].url == "https://www.scribd.com/document/123456/Alice-Resume"


# --------------------------------------------------- factory + resolver path


async def test_make_web_search_fn_prefers_api_key_over_oauth(httpx_mock: HTTPXMock) -> None:
    """When both credentials exist, the Platform API path wins."""
    httpx_mock.add_response(
        url=OPENAI_RESPONSES_URL,
        method="POST",
        content=_TWO_CITATIONS_SSE,
        headers={"Content-Type": "text/event-stream"},
    )
    async with httpx.AsyncClient() as client:
        fn = make_web_search_fn(
            client=client,
            api_key="sk-test",
            oauth_credentials=_fresh_credentials(),
        )
        hits = await fn("alice")
    assert hits
    # Confirm only the Platform endpoint was hit, not the Codex backend.
    urls_called = {str(req.url) for req in httpx_mock.get_requests()}
    assert OPENAI_RESPONSES_URL in urls_called
    assert _OAUTH_URL not in urls_called


async def test_make_web_search_fn_falls_back_to_oauth(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_OAUTH_URL,
        method="POST",
        content=_TWO_CITATIONS_SSE,
        headers={"Content-Type": "text/event-stream"},
    )
    async with httpx.AsyncClient() as client:
        fn = make_web_search_fn(
            client=client,
            api_key=None,
            oauth_credentials=_fresh_credentials(),
        )
        hits = await fn("alice")
    assert hits


async def test_oauth_falls_back_to_text_extraction_when_annotations_empty(
    httpx_mock: HTTPXMock,
) -> None:
    """Codex backend lists URLs in message text, not annotations.

    Mirrors the production stream observed against
    ``chatgpt.com/backend-api/codex/responses``: the ``web_search`` tool
    completes, the model writes a final assistant message containing
    bare ``https://`` URLs in its text block, but ``annotations`` is
    empty. Without text-extraction the helper used to return zero hits,
    so the eight SPA doc-leak platforms surfaced as
    ``presence_status="not_found"`` even when the search succeeded.
    """
    httpx_mock.add_response(
        url=_OAUTH_URL,
        method="POST",
        content=_OAUTH_BARE_URLS_IN_TEXT_SSE,
        headers={"Content-Type": "text/event-stream"},
    )
    creds = _fresh_credentials()
    async with httpx.AsyncClient() as client:
        hits = await web_search_via_chatgpt_oauth(
            "site:scribd.com alice",
            credentials=creds,
            client=client,
        )
    urls = [h.url for h in hits]
    # Deduplicates the repeated scribd URL and trims the trailing ``)``
    # off the parenthesised issuu URL so each result is the canonical
    # platform link a downstream regex can validate.
    assert urls == [
        "https://www.scribd.com/document/111/Alice-Resume",
        "https://www.scribd.com/document/222/Alice-CV",
        "https://issuu.com/alice/docs/notes",
    ]


async def test_make_web_search_fn_raises_when_no_credentials() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(WebSearchUnavailableError):
            make_web_search_fn(client=client, api_key=None, oauth_credentials=None)


# --------------------------------------------------- WebSearchHit identity


async def test_429_raises_rate_limit_subclass(httpx_mock: HTTPXMock) -> None:
    """HTTP 429 surfaces as :class:`WebSearchRateLimitError`, not generic.

    The dedicated subclass lets the retry wrapper apply a longer
    backoff (rate-limit windows are tens of seconds) and lets the
    collectors categorise the trace marker as ``rate_limited`` so
    analysts can tell quota exhaustion apart from a real backend bug.
    """
    httpx_mock.add_response(
        url=OPENAI_RESPONSES_URL,
        method="POST",
        status_code=429,
        text="rate limit",
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(WebSearchRateLimitError) as exc_info:
            await web_search_via_platform_api(
                "alice",
                api_key="sk-test",
                client=client,
            )
    # WebSearchRateLimitError is a subclass of WebSearchError so
    # existing ``except WebSearchError`` handlers keep working.
    assert isinstance(exc_info.value, WebSearchError)
    assert "429" in str(exc_info.value)


async def test_make_web_search_fn_retries_on_rate_limit(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wrapper retries an HTTP 429 once and returns the second-attempt hits.

    Patches the backoff sleeper to a no-op so the test stays fast.
    """
    from reckora.reasoning import web_search as ws_mod

    async def _no_sleep(*_a: object, **_kw: object) -> None:
        return None

    monkeypatch.setattr(ws_mod, "_sleep_with_backoff", _no_sleep)

    # First call: 429. Second call: success.
    httpx_mock.add_response(
        url=OPENAI_RESPONSES_URL,
        method="POST",
        status_code=429,
        text="rate limit",
    )
    httpx_mock.add_response(
        url=OPENAI_RESPONSES_URL,
        method="POST",
        content=_TWO_CITATIONS_SSE,
        headers={"Content-Type": "text/event-stream"},
    )

    async with httpx.AsyncClient() as client:
        fn = make_web_search_fn(client=client, api_key="sk-test")
        hits = await fn("alice")

    assert len(hits) == 2  # citations from the second-attempt SSE
    # Two requests issued — first 429, second OK.
    assert len(httpx_mock.get_requests()) == 2


def test_websearchhit_is_immutable() -> None:
    """The dataclass is frozen so hits can be deduplicated via sets."""
    from dataclasses import FrozenInstanceError

    hit = WebSearchHit(url="https://example.com", title="ex")
    with pytest.raises(FrozenInstanceError):
        hit.url = "https://other.example"  # type: ignore[misc]
