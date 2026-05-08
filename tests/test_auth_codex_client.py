"""Unit tests for ``reckora.auth.codex_client`` (SSE consumer)."""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.auth.codex_client import CodexStreamError, complete_via_codex
from reckora.auth.oauth import CHATGPT_CODEX_BASE_URL


def _sse(*events: str) -> bytes:
    """Encode JSON event payloads as a server-sent-events byte stream."""
    return ("\n\n".join(f"data: {event}" for event in events) + "\n\n").encode("utf-8")


async def test_concatenates_output_text_deltas(httpx_mock: HTTPXMock) -> None:
    body = _sse(
        '{"type":"response.output_text.delta","delta":"Hello "}',
        '{"type":"response.output_text.delta","delta":"world"}',
        '{"type":"response.completed"}',
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        content=body,
        headers={"content-type": "text/event-stream"},
    )

    async with httpx.AsyncClient() as client:
        out = await complete_via_codex(
            model="gpt-5.1-codex-mini",
            system="be brief",
            user="hi",
            access_token="bearer-abc",
            client=client,
        )
    assert out == "Hello world"


async def test_request_carries_bearer_token_and_codex_body_shape(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        content=_sse('{"type":"response.completed"}'),
        headers={"content-type": "text/event-stream"},
    )

    async with httpx.AsyncClient() as client:
        await complete_via_codex(
            model="gpt-5.1-codex-mini",
            system="sys",
            user="usr",
            access_token="atk-xyz",
            client=client,
        )

    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["Authorization"] == "Bearer atk-xyz"
    assert request.headers["Accept"] == "text/event-stream"

    import json

    body = json.loads(request.read())
    assert body["model"] == "gpt-5.1-codex-mini"
    assert body["instructions"] == "sys"
    assert body["stream"] is True
    # Codex Responses API expects a typed input array, not a flat list.
    assert body["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "usr"}]}]


async def test_falls_back_to_completed_event_when_no_deltas(
    httpx_mock: HTTPXMock,
) -> None:
    """Some Codex models batch the entire response into the
    ``response.completed`` event instead of streaming deltas."""
    body = _sse(
        '{"type":"response.completed",'
        '"response":{"output":[{"content":[{"type":"output_text","text":"batched answer"}]}]}}',
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        content=body,
        headers={"content-type": "text/event-stream"},
    )

    async with httpx.AsyncClient() as client:
        out = await complete_via_codex(
            model="gpt-5.1-codex-mini",
            system="s",
            user="u",
            access_token="atk",
            client=client,
        )
    assert out == "batched answer"


async def test_returns_empty_string_when_stream_carries_no_text(
    httpx_mock: HTTPXMock,
) -> None:
    body = _sse('{"type":"response.completed"}')
    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        content=body,
        headers={"content-type": "text/event-stream"},
    )

    async with httpx.AsyncClient() as client:
        out = await complete_via_codex(
            model="m",
            system="s",
            user="u",
            access_token="atk",
            client=client,
        )
    assert out == ""


async def test_stream_error_event_raises(httpx_mock: HTTPXMock) -> None:
    body = _sse(
        '{"type":"response.output_text.delta","delta":"partial..."}',
        '{"type":"error","message":"upstream blew up"}',
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        content=body,
        headers={"content-type": "text/event-stream"},
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(CodexStreamError, match="upstream blew up"):
            await complete_via_codex(
                model="m",
                system="s",
                user="u",
                access_token="atk",
                client=client,
            )


async def test_http_4xx_propagates_as_status_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        status_code=401,
        json={"error": "invalid_token"},
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await complete_via_codex(
                model="m",
                system="s",
                user="u",
                access_token="atk",
                client=client,
            )
    assert exc_info.value.response.status_code == 401


async def test_tolerates_done_sentinel_and_unknown_events(
    httpx_mock: HTTPXMock,
) -> None:
    """Unknown event types and ``[DONE]`` lines must not crash the parser."""
    body = (
        b'data: {"type":"response.created"}\n\n'
        b'data: {"type":"response.output_text.delta","delta":"a"}\n\n'
        b'event: ping\ndata: {"type":"ping"}\n\n'
        b"data: [DONE]\n\n"
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        content=body,
        headers={"content-type": "text/event-stream"},
    )

    async with httpx.AsyncClient() as client:
        out = await complete_via_codex(
            model="m",
            system="s",
            user="u",
            access_token="atk",
            client=client,
        )
    assert out == "a"


async def test_tolerates_invalid_json_data_lines(httpx_mock: HTTPXMock) -> None:
    body = b'data: not json\n\ndata: {"type":"response.output_text.delta","delta":"ok"}\n\n'
    httpx_mock.add_response(
        method="POST",
        url=f"{CHATGPT_CODEX_BASE_URL}/responses",
        content=body,
        headers={"content-type": "text/event-stream"},
    )

    async with httpx.AsyncClient() as client:
        out = await complete_via_codex(
            model="m",
            system="s",
            user="u",
            access_token="atk",
            client=client,
        )
    assert out == "ok"


async def test_base_url_is_overridable(httpx_mock: HTTPXMock) -> None:
    """Tests / staging deployments can swap to a mock backend."""
    custom = "https://staging.example.com/codex"
    httpx_mock.add_response(
        method="POST",
        url=f"{custom}/responses",
        content=_sse('{"type":"response.completed"}'),
        headers={"content-type": "text/event-stream"},
    )

    async with httpx.AsyncClient() as client:
        await complete_via_codex(
            model="m",
            system="s",
            user="u",
            access_token="atk",
            client=client,
            base_url=custom,
        )

    request = httpx_mock.get_request()
    assert request is not None
    assert str(request.url) == f"{custom}/responses"
