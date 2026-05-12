"""LLM-backed web search via the OpenAI Responses API.

OSINT collectors that scrape per-site search SERPs (``doc_leak``) hit
two recurring failure modes that direct ``httpx.get`` can't bypass:

* JS-rendered search pages (scribd / slideshare / issuu) return a small
  SPA shell with zero hit URLs in the initial HTML — the actual results
  arrive via XHR after a logged-in session is established.
* Cloudflare / anti-bot interstitials (dokumen.tips / calameo / 4shared)
  serve a ``200`` "checking your browser" challenge in lieu of results.

The reliable workaround is to delegate the search to OpenAI's Responses
API with the built-in ``web_search`` tool: OpenAI runs the query
against Bing's index, returns the top hits as ``url_citation``
annotations, and we anchor those URLs to each platform's known canonical
shape via the per-site regexes in :mod:`reckora.collectors.doc_leak`.

Two transport paths, mirroring :class:`reckora.reasoning.client.ReasoningClient`:

* ``api.openai.com/v1/responses`` — when ``OPENAI_API_KEY`` is set.
  Uses the documented ``web_search_preview`` tool type; the model bills
  against the Platform API tier.
* ``chatgpt.com/backend-api/codex/responses`` — when ChatGPT OAuth
  credentials are present (``reckora auth login``). Uses the Codex
  backend's ``web_search`` tool type; bills against the user's ChatGPT
  Plus subscription, no API credits required.

Both endpoints stream Server-Sent Events. We accumulate ``url_citation``
annotations across all ``response.output_item.done`` /
``response.completed`` payloads and return them as
:class:`WebSearchHit` records — the assistant text itself is discarded
because callers only need the URLs (the per-site regex validates that
each URL is genuinely from the target domain).
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from ..auth.oauth import CHATGPT_CODEX_BASE_URL, OAuthCredentials

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

# Bing-backed ``web_search_preview`` is the documented tool type on
# ``api.openai.com``. The Codex backend at ``chatgpt.com`` exposes the
# same capability under the shorter ``web_search`` name — ChatGPT's own
# browse feature and the Codex CLI both use this form.
PLATFORM_TOOL_TYPE = "web_search_preview"
CODEX_TOOL_TYPE = "web_search"

# Default model for each transport. The Platform model needs to support
# the ``web_search_preview`` tool (gpt-4o family is the documented
# baseline); the Codex backend hosts a different lineup (``gpt-5.5``
# is the current ChatGPT Plus default).
DEFAULT_PLATFORM_MODEL = "gpt-4o-mini"
DEFAULT_CODEX_MODEL = "gpt-5.5"

# System prompt threaded through ``instructions`` on the Responses-API
# request. Two roles:
#
#   1. The Codex backend rejects requests that omit ``instructions`` with
#      ``HTTP 400 {"detail":"Instructions are required"}`` — historically
#      optional, enforced in 2026.
#   2. The Codex backend's ``web_search`` tool result is NOT surfaced to
#      callers as ``url_citation`` annotations (only the Platform path's
#      ``web_search_preview`` tool does that). The model has to write the
#      result URLs into the final assistant message text instead, where
#      :func:`_extract_urls_from_text` picks them up. The prompt therefore
#      *requires* the model to produce a non-empty final message listing
#      every relevant URL on its own line; otherwise the call returns
#      zero hits even when the tool ran successfully.
DEFAULT_INSTRUCTIONS = (
    "You are a focused web-search assistant for OSINT investigations. "
    "For each user query, call the web_search tool exactly once with the "
    "query verbatim. After the tool returns, you MUST produce a final "
    "assistant message that lists every relevant result URL on its own "
    "line. Do not paraphrase the query, do not invent results, do not "
    "omit URLs the tool returned, and never produce an empty final "
    "message — the caller parses the URLs out of that message text."
)

# Recognises bare ``https://``/``http://`` URLs inside assistant message
# text. Used by :func:`_extract_urls_from_text` as a fallback when the
# Codex backend skips ``url_citation`` annotations. Stops at whitespace,
# common bracketing characters, and trailing punctuation so URLs from
# prose-style listings (``"...site (https://...)."``) round-trip cleanly.
_URL_RE = re.compile(r"https?://[^\s<>\"\)\]\}]+")
_URL_TRIM_TRAILING = ".,;:!?"


@dataclass(frozen=True)
class WebSearchHit:
    """One ``url_citation`` extracted from a Responses-API stream.

    ``snippet`` is the assistant text slice that the citation anchored
    to (the model's one-line summary of the page). Often empty when the
    search tool runs in tool-only mode without follow-up generation.
    """

    url: str
    title: str
    snippet: str = ""


class WebSearchError(RuntimeError):
    """The Responses API stream errored, timed out, or returned a
    body shape we don't recognise."""


class WebSearchRateLimitError(WebSearchError):
    """Backend rate-limited us (HTTP 429).

    Surfaced as a distinct subclass so the retry wrapper in
    :func:`make_web_search_fn` can apply a longer backoff and so
    collectors can categorise the failure in their trace markers
    (``backend rate_limited`` vs the generic ``backend error``).
    """


class WebSearchUnavailableError(RuntimeError):
    """No backend is configured to satisfy a ``web_search`` call.

    Raised by :func:`make_web_search_fn` when neither
    ``OPENAI_API_KEY`` is set nor a valid set of ChatGPT OAuth
    credentials is on disk. Callers (collectors) catch this and degrade
    gracefully — they emit a trace marked ``unverified`` rather than
    falsely concluding the platform has no hits.
    """


WebSearchFn = Callable[[str], Awaitable[list[WebSearchHit]]]
"""Callable signature: ``await fn(query) -> [WebSearchHit, ...]``.

``query`` is a fully-formed search-engine query (e.g.
``site:scribd.com "alice"``). Implementations cap the returned list at
their own ``limit`` argument; callers further trim downstream.
"""

# Default concurrency cap on simultaneous backend calls. Codex OAuth
# accounts are rate-limited per ChatGPT Plus subscription, so fanning
# out 13 queries at once (5 leak_hunt + 8 doc_leak) reliably trips the
# limit. Three concurrent calls keep the per-investigation wall clock
# reasonable (~20-30s) while staying inside the documented quota.
DEFAULT_MAX_CONCURRENCY = 3

# Default retry policy for transient backend failures. Applied to HTTP
# 429, HTTP 5xx, and stream timeouts. Total worst-case wall clock is
# ~12s (1 + 3 + 8 second sleeps before each retry) per query.
DEFAULT_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 1.0  # seconds; doubled each attempt with jitter


async def web_search_via_platform_api(
    query: str,
    *,
    api_key: str,
    client: httpx.AsyncClient,
    limit: int = 10,
    model: str = DEFAULT_PLATFORM_MODEL,
    instructions: str = DEFAULT_INSTRUCTIONS,
    timeout: float = 25.0,
) -> list[WebSearchHit]:
    """Run a single ``web_search_preview`` call against ``api.openai.com``.

    Returns up to ``limit`` :class:`WebSearchHit` records parsed from
    ``url_citation`` annotations on the assistant message items. Raises
    :class:`WebSearchError` if the stream errors or the response is
    malformed.
    """
    body = _build_request_body(
        query=query,
        model=model,
        tool_type=PLATFORM_TOOL_TYPE,
        store=True,
        instructions=instructions,
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    return await _stream_and_parse(
        url=OPENAI_RESPONSES_URL,
        body=body,
        headers=headers,
        client=client,
        timeout=timeout,
        limit=limit,
    )


async def web_search_via_chatgpt_oauth(
    query: str,
    *,
    credentials: OAuthCredentials,
    client: httpx.AsyncClient,
    limit: int = 10,
    model: str = DEFAULT_CODEX_MODEL,
    base_url: str = CHATGPT_CODEX_BASE_URL,
    instructions: str = DEFAULT_INSTRUCTIONS,
    timeout: float = 25.0,
) -> list[WebSearchHit]:
    """Run a single ``web_search`` call against the ChatGPT Codex backend.

    Uses the same ``access_token`` flow as
    :func:`reckora.auth.codex_client.complete_with_tools_via_codex`.
    The Codex backend rejects requests that leave ``store`` unset or
    truthy — we always send ``store=False`` here to match the rest of
    the OAuth code path. It also rejects requests that omit
    ``instructions`` (``HTTP 400 {"detail":"Instructions are required"}``),
    so the helper always threads a sensible default through
    :data:`DEFAULT_INSTRUCTIONS`.
    """
    body = _build_request_body(
        query=query,
        model=model,
        tool_type=CODEX_TOOL_TYPE,
        store=False,
        instructions=instructions,
    )
    headers = {
        "Authorization": f"Bearer {credentials.access_token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    return await _stream_and_parse(
        url=f"{base_url}/responses",
        body=body,
        headers=headers,
        client=client,
        timeout=timeout,
        limit=limit,
    )


def make_web_search_fn(
    *,
    client: httpx.AsyncClient,
    api_key: str | None = None,
    oauth_credentials: OAuthCredentials | None = None,
    platform_model: str = DEFAULT_PLATFORM_MODEL,
    codex_model: str = DEFAULT_CODEX_MODEL,
    instructions: str = DEFAULT_INSTRUCTIONS,
    timeout: float = 25.0,
    limit: int = 10,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> WebSearchFn:
    """Resolve a :data:`WebSearchFn` from whichever credential is available.

    Resolution order matches :class:`ReasoningClient`'s ``auto`` mode:
    Platform API key (cheapest, documented) > ChatGPT OAuth (uses Plus
    subscription, no API credits). Raises :class:`WebSearchUnavailableError`
    when neither is configured — orchestrators construct the function
    once at startup and inject it into collectors, so failing fast here
    surfaces the misconfiguration before any traces are emitted.

    The returned function is wrapped with two layers of resilience:

    * An :class:`asyncio.Semaphore` (``max_concurrency``) caps the number
      of in-flight backend calls. Multiple collectors invoking the same
      ``WebSearchFn`` concurrently (``doc_leak`` + ``leak_hunt`` both
      fan out 8-12 queries) reliably trip per-account rate limits on
      ChatGPT OAuth; throttling here keeps the per-investigation
      success rate close to 100% at the cost of slightly higher wall
      clock.
    * Retry-with-jittered-exponential-backoff on
      :class:`WebSearchRateLimitError` (HTTP 429) and HTTP 5xx /
      transport errors. Retries are bounded by ``max_retries``;
      :class:`WebSearchError` is re-raised after the budget is
      exhausted so collectors can still emit a ``blocked`` trace.
    """
    semaphore = asyncio.Semaphore(max_concurrency)
    if api_key:

        async def _via_api(query: str) -> list[WebSearchHit]:
            async with semaphore:
                return await _with_retry(
                    lambda: web_search_via_platform_api(
                        query,
                        api_key=api_key,
                        client=client,
                        limit=limit,
                        model=platform_model,
                        instructions=instructions,
                        timeout=timeout,
                    ),
                    max_retries=max_retries,
                )

        return _via_api
    if oauth_credentials is not None:
        creds = oauth_credentials

        async def _via_oauth(query: str) -> list[WebSearchHit]:
            async with semaphore:
                return await _with_retry(
                    lambda: web_search_via_chatgpt_oauth(
                        query,
                        credentials=creds,
                        client=client,
                        limit=limit,
                        model=codex_model,
                        instructions=instructions,
                        timeout=timeout,
                    ),
                    max_retries=max_retries,
                )

        return _via_oauth
    raise WebSearchUnavailableError(
        "no web-search backend configured — set OPENAI_API_KEY or "
        "run `reckora auth login` to enable ChatGPT-OAuth web search."
    )


async def _with_retry(
    call: Callable[[], Awaitable[list[WebSearchHit]]],
    *,
    max_retries: int,
) -> list[WebSearchHit]:
    """Retry a backend call with jittered exponential backoff.

    Distinguishes :class:`WebSearchRateLimitError` (HTTP 429) from
    generic :class:`WebSearchError`:

    * Rate-limit hits get a longer first backoff (``base * 4``) because
      OpenAI's quota window is typically tens of seconds; retrying
      sooner just wastes the budget.
    * Generic transient errors use the standard ``base * 2**attempt``
      curve, capped at ``_RETRY_BACKOFF_BASE * 2**max_retries``.

    Non-:class:`WebSearchError` exceptions (e.g. programmer error in
    the parser) bubble up immediately — we only retry what we know is
    transient.
    """
    last_exc: WebSearchError | None = None
    for attempt in range(max_retries + 1):
        try:
            return await call()
        except WebSearchRateLimitError as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            await _sleep_with_backoff(attempt, scale=4.0)
        except WebSearchError as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            # Only retry transport errors / 5xx. HTTP 4xx (other than
            # 429, which is its own subclass above) are caller bugs;
            # retrying won't fix them.
            message = str(exc).lower()
            transient = (
                "transport" in message
                or any(f"http 5{n:02d}" in message for n in range(100))
                or "timeout" in message
            )
            if not transient:
                break
            await _sleep_with_backoff(attempt, scale=1.0)
    # Either the loop exhausted attempts or hit a non-transient error.
    # ``last_exc`` is guaranteed set because at least one attempt ran
    # and raised (success returns inside the try).
    assert last_exc is not None  # for mypy; the loop guarantees this
    raise last_exc


async def _sleep_with_backoff(attempt: int, *, scale: float = 1.0) -> None:
    """Sleep ``scale * base * 2**attempt`` seconds with up to 25% jitter.

    Jitter prevents synchronised retry storms when multiple collectors
    hit a rate limit in lockstep — they otherwise wake up together and
    re-trigger the same 429.
    """
    base = _RETRY_BACKOFF_BASE * scale * (2**attempt)
    jitter = random.uniform(0.0, base * 0.25)
    await asyncio.sleep(base + jitter)


def _build_request_body(
    *,
    query: str,
    model: str,
    tool_type: str,
    store: bool,
    instructions: str = DEFAULT_INSTRUCTIONS,
) -> dict[str, Any]:
    """Construct the Responses-API request body for a tool-only call.

    ``tool_choice`` is pinned to the same tool type as ``tools`` so the
    model can't decide to skip the search and answer from priors —
    we want deterministic search behaviour for OSINT. ``instructions``
    is required by the Codex backend (omitting it returns
    ``HTTP 400 {"detail":"Instructions are required"}``) and harmless on
    the Platform path, so it is always sent.
    """
    return {
        "model": model,
        "instructions": instructions,
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": query}],
            }
        ],
        "tools": [{"type": tool_type}],
        "tool_choice": {"type": tool_type},
        "stream": True,
        "store": store,
    }


async def _stream_and_parse(
    *,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    client: httpx.AsyncClient,
    timeout: float,
    limit: int,
) -> list[WebSearchHit]:
    """POST to ``url``, stream the SSE response, harvest URL citations."""
    try:
        async with client.stream(
            "POST",
            url,
            json=body,
            headers=headers,
            timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            hits: list[WebSearchHit] = []
            seen: set[str] = set()
            async for event in _iter_sse_events(resp.aiter_lines()):
                event_type = event.get("type")
                if event_type == "response.output_item.done":
                    _collect_citations_from_item(event.get("item"), hits, seen, limit)
                elif event_type == "response.completed":
                    _collect_citations_from_completed(event, hits, seen, limit)
                elif event_type == "error":
                    msg = event.get("message") or event.get("error") or "stream error"
                    raise WebSearchError(str(msg))
                if len(hits) >= limit:
                    break
            return hits[:limit]
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code == 429:
            raise WebSearchRateLimitError(
                f"web-search backend rate limited (HTTP {status_code})",
            ) from exc
        raise WebSearchError(
            f"web-search backend returned HTTP {status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise WebSearchError(f"web-search transport error: {type(exc).__name__}") from exc


async def _iter_sse_events(
    lines: AsyncIterator[str],
) -> AsyncIterator[dict[str, Any]]:
    """Yield decoded JSON event objects from an SSE byte stream.

    Mirrors :func:`reckora.auth.codex_client._iter_sse_events` — kept
    private here so the web-search module has no test-time coupling to
    the codex client's parser (the wire grammar is shared and
    well-defined).
    """
    async for raw in lines:
        if not raw or raw.startswith(":"):
            continue
        if not raw.startswith("data:"):
            continue
        payload = raw[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            yield data


def _collect_citations_from_item(
    item: Any,
    hits: list[WebSearchHit],
    seen: set[str],
    limit: int,
) -> None:
    """Extract URL hits from one ``output_item.done`` message event.

    Output items that carry citations are of type ``message`` with
    ``content`` blocks of type ``output_text``. Two extraction paths:

    1. ``url_citation`` entries in the block's ``annotations`` array —
       used by ``api.openai.com``'s ``web_search_preview`` tool and the
       documented Responses-API shape.
    2. Bare URLs in the block's ``text`` — fallback for backends that
       don't auto-annotate (notably ``chatgpt.com/backend-api/codex/
       responses``: its ``web_search`` tool returns results to the model
       in-context but never emits ``url_citation`` records, so the model
       must re-write them in the final message text per
       :data:`DEFAULT_INSTRUCTIONS`).

    Web-search-tool result items themselves (``type: web_search_call``)
    don't carry the URLs — the URLs land on the *next* assistant message
    that summarises them.
    """
    if not isinstance(item, dict):
        return
    if item.get("type") != "message":
        return
    content = item.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        block_text = block.get("text")
        text = block_text if isinstance(block_text, str) else ""
        annotations = block.get("annotations")
        annotations_yielded_hit = False
        if isinstance(annotations, list):
            for ann in annotations:
                if len(hits) >= limit:
                    return
                hit = _hit_from_annotation(ann, text)
                if hit is None or hit.url in seen:
                    continue
                seen.add(hit.url)
                hits.append(hit)
                annotations_yielded_hit = True
        if annotations_yielded_hit:
            # Trust the structured citations when present; mixing with
            # text-scraped URLs from the same block risks counting one
            # hit twice with mismatched titles.
            continue
        for hit in _extract_urls_from_text(text, limit - len(hits)):
            if hit.url in seen:
                continue
            seen.add(hit.url)
            hits.append(hit)
            if len(hits) >= limit:
                return


def _collect_citations_from_completed(
    event: dict[str, Any],
    hits: list[WebSearchHit],
    seen: set[str],
    limit: int,
) -> None:
    """Walk the ``response.completed`` payload as a final-chance fallback.

    Some backends emit only the consolidated ``response.completed``
    event without per-item ``response.output_item.done`` signals. We
    re-scan the full ``output`` array here to cover that case
    idempotently — :data:`seen` prevents double-counting against the
    streaming path.
    """
    response = event.get("response")
    if not isinstance(response, dict):
        return
    output = response.get("output")
    if not isinstance(output, list):
        return
    for item in output:
        if len(hits) >= limit:
            return
        _collect_citations_from_item(item, hits, seen, limit)


def _extract_urls_from_text(text: str, remaining: int) -> list[WebSearchHit]:
    """Scrape bare ``http(s)`` URLs out of an assistant message body.

    Used as a fallback when the Responses-API stream returns no
    structured ``url_citation`` annotations — most commonly on the
    Codex OAuth backend. Caps the result list at ``remaining`` so the
    caller's overall ``limit`` budget is preserved. Each returned
    :class:`WebSearchHit` carries the raw URL as its title; the per-site
    canonical regex in :mod:`reckora.collectors.doc_leak` does the
    relevance gating downstream.
    """
    if remaining <= 0 or not text:
        return []
    out: list[WebSearchHit] = []
    seen_local: set[str] = set()
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(_URL_TRIM_TRAILING)
        if not url or url in seen_local:
            continue
        seen_local.add(url)
        out.append(WebSearchHit(url=url, title=url, snippet=""))
        if len(out) >= remaining:
            break
    return out


def _hit_from_annotation(ann: Any, text: str) -> WebSearchHit | None:
    """Materialise one :class:`WebSearchHit` from a citation annotation.

    Returns ``None`` for annotation shapes we don't recognise (e.g.
    file citations, container-file pointers) — those aren't web hits
    and shouldn't pollute the result list.
    """
    if not isinstance(ann, dict):
        return None
    if ann.get("type") != "url_citation":
        return None
    url = ann.get("url")
    if not isinstance(url, str) or not url:
        return None
    title_raw = ann.get("title")
    title = title_raw if isinstance(title_raw, str) else ""
    start = ann.get("start_index")
    end = ann.get("end_index")
    snippet = ""
    if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
        snippet = text[start:end].strip()
    return WebSearchHit(url=url, title=title, snippet=snippet)
