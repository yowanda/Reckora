"""Tool implementations the AgentLoop can expose to the reasoning LLM.

Why tools at all? The Phase 4 AgentLoop (`agent/loop.py`) was designed
as "AI proposes, rules dispose": the LLM looked at the trace set the
collectors had already produced and proposed which *known* identifiers
to investigate next. That's evidence-bounded by construction, but it
also means the AI cannot widen the search beyond what the rule-based
collectors stumbled across. If GitHub doesn't surface a personal blog
URL in a profile bio, the AI has nothing to chase.

The tool layer fixes that. We expose two builtin tools to the LLM:

* ``web_search(query, max_results)`` — runs a DuckDuckGo HTML search
  and returns the top result rows (title, URL, snippet). No API key
  required.
* ``fetch_url(url)`` — fetches a public URL with a sane size /
  content-type cap, extracts readable text, and returns the title +
  body excerpt.

Crucially, **every tool invocation produces a real Trace + Evidence
row**, with ``source = TraceSource.WEB_RESEARCH``, the canonical
SHA-256 of the payload, and the URL/query as the evidence ``source_url``.
That's how we keep the chain-of-custody guarantee while letting the
LLM "browse": the trace is the only way the LLM can later cite the
finding (the verifier still requires ``evidence_refs`` to point at a
real on-disk SHA prefix), so a hallucinated tool result has no path
into a final dossier.

The actual tool-call orchestration lives in
:mod:`reckora.agent.research`; this module is just the inventory.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolSpec:
    """A single tool exposed to the reasoning LLM.

    ``schema`` follows the JSON-Schema dialect OpenAI's chat-completions
    ``tools`` parameter expects: a top-level object with
    ``properties`` / ``required``.
    """

    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[ToolResult]]

    def to_openai(self) -> dict[str, Any]:
        """Render the spec as an OpenAI Chat-Completions ``tools=[...]`` entry."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }

    def to_responses(self) -> dict[str, Any]:
        """Render the spec as an OpenAI Responses-API ``tools=[...]`` entry.

        The Responses API (used by the ChatGPT Codex backend that
        OAuth authentication targets) flattens the function definition
        — there is no nested ``function`` wrapper.
        """
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.schema,
        }


@dataclass(frozen=True)
class ToolResult:
    """Outcome of a single tool call.

    ``content`` is the JSON-serialisable payload we feed back to the
    LLM as the ``tool`` message body. ``trace`` is the materialised
    evidence row that lands in the investigation graph; the
    correlation engine and the verifier treat it the same as any
    collector-emitted trace.
    """

    content: dict[str, Any]
    trace: Trace | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# DuckDuckGo HTML search backend
# ---------------------------------------------------------------------------

_DDG_HTML_ENDPOINT = "https://duckduckgo.com/html/"
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _clean_html_text(raw: str) -> str:
    return _WHITESPACE_RE.sub(" ", _TAG_STRIP_RE.sub("", raw)).strip()


async def _ddg_search(
    *,
    query: str,
    max_results: int,
    client: httpx.AsyncClient,
) -> list[dict[str, str]]:
    """Run a DDG HTML search and parse the result rows.

    DuckDuckGo's HTML endpoint is intentionally low-friction (no API
    key, no JS) but its markup occasionally changes. We tolerate a
    parse miss as "no results" rather than crashing.
    """
    resp = await client.post(
        _DDG_HTML_ENDPOINT,
        data={"q": query},
        headers={
            "User-Agent": "Reckora/1.0 (+https://reckora.my.id)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    resp.raise_for_status()
    body = resp.text

    rows: list[dict[str, str]] = []
    for match in _DDG_RESULT_RE.finditer(body):
        url = match.group(1).strip()
        title = _clean_html_text(match.group(2))
        snippet = _clean_html_text(match.group(3))
        if not url or not title:
            continue
        rows.append({"url": url, "title": title, "snippet": snippet})
        if len(rows) >= max_results:
            break
    return rows


# ---------------------------------------------------------------------------
# fetch_url body extraction
# ---------------------------------------------------------------------------

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.IGNORECASE | re.DOTALL)


def _extract_readable(body: str, *, max_chars: int) -> tuple[str, str | None]:
    """Return ``(text, title)`` extracted from raw HTML.

    Stripped of script/style blocks and HTML tags. Truncated at
    ``max_chars`` so a 5MB blog post doesn't blow the LLM context
    window.
    """
    title_match = _TITLE_RE.search(body)
    title = _clean_html_text(title_match.group(1)) if title_match else None

    body_match = _BODY_RE.search(body)
    fragment = body_match.group(1) if body_match else body
    fragment = _SCRIPT_STYLE_RE.sub(" ", fragment)
    text = _clean_html_text(fragment)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text, title


# ---------------------------------------------------------------------------
# Public tool builders
# ---------------------------------------------------------------------------


@dataclass
class ToolBudget:
    """Per-iteration limits applied to all tools.

    Centralised so the AgentLoop can enforce a single budget across
    every tool invocation rather than each tool clamping
    independently. ``calls_remaining`` is mutated by the runner; the
    initial values are CLI-tunable.
    """

    calls_remaining: int = 8
    fetch_max_bytes: int = 64 * 1024
    fetch_max_chars: int = 6 * 1024
    request_timeout: float = 8.0

    def consume(self) -> bool:
        if self.calls_remaining <= 0:
            return False
        self.calls_remaining -= 1
        return True


def builtin_tools(
    *,
    seed: Identifier,
    budget: ToolBudget,
    client_factory: Callable[[], Awaitable[httpx.AsyncClient]] | None = None,
) -> list[ToolSpec]:
    """Build the default ``[web_search, fetch_url]`` tool inventory.

    ``client_factory`` lets tests inject a stub ``httpx.AsyncClient``
    so we don't go to the live internet during unit tests. Production
    callers leave it ``None`` and a fresh client is constructed per
    invocation.
    """

    async def _client() -> httpx.AsyncClient:
        if client_factory is not None:
            return await client_factory()
        return httpx.AsyncClient(
            timeout=budget.request_timeout,
            follow_redirects=True,
        )

    async def web_search(args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult(
                content={"error": "missing 'query' argument"},
                error="missing query",
            )
        max_results = int(args.get("max_results", 5))
        max_results = max(1, min(max_results, 8))
        client = await _client()
        async with _maybe_close(client, owns=client_factory is None):
            try:
                rows = await _ddg_search(
                    query=query,
                    max_results=max_results,
                    client=client,
                )
            except httpx.HTTPError as exc:
                log.warning("web_search transport error: %s", exc)
                return ToolResult(
                    content={"error": f"transport error: {exc}"},
                    error=str(exc),
                )
        payload = {
            "query": query,
            "results": rows,
        }
        evidence = make_evidence(
            f"https://duckduckgo.com/?q={quote_plus(query)}",
            payload,
        )
        # The seed is used as the trace's identifier so the resulting
        # row is anchored to the investigation graph. Correlation
        # rules don't traverse web_research traces directly — the LLM
        # is expected to cite specific URLs / fields next.
        trace = Trace(
            identifier=seed,
            source=TraceSource.WEB_RESEARCH,
            fields={
                "tool": "web_search",
                "query": query,
                "result_count": len(rows),
                "top_urls": [r["url"] for r in rows[:5]],
            },
            evidence=evidence,
        )
        return ToolResult(
            content={
                **payload,
                "evidence_sha256": evidence.payload_sha256,
            },
            trace=trace,
        )

    async def fetch_url(args: dict[str, Any]) -> ToolResult:
        url = str(args.get("url", "")).strip()
        if not url:
            return ToolResult(
                content={"error": "missing 'url' argument"},
                error="missing url",
            )
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ToolResult(
                content={"error": f"unsupported url: {url!r}"},
                error="unsupported scheme",
            )
        client = await _client()
        async with _maybe_close(client, owns=client_factory is None):
            try:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": "Reckora/1.0 (+https://reckora.my.id)",
                        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                    },
                )
            except httpx.HTTPError as exc:
                log.warning("fetch_url transport error: %s", exc)
                return ToolResult(
                    content={"error": f"transport error: {exc}"},
                    error=str(exc),
                )
        body = resp.text
        if len(body.encode("utf-8")) > budget.fetch_max_bytes:
            body = body.encode("utf-8")[: budget.fetch_max_bytes].decode(
                "utf-8",
                errors="ignore",
            )
        text, title = _extract_readable(body, max_chars=budget.fetch_max_chars)
        payload = {
            "url": url,
            "status_code": resp.status_code,
            "title": title,
            "text": text,
        }
        evidence = make_evidence(url, payload)
        # Try to express the fetched URL as a typed Identifier so the
        # resulting trace can participate in correlation. Falls back
        # to the seed when the URL cannot be coerced.
        try:
            ident = Identifier(type=IdentifierType.URL, value=url)
        except Exception:
            ident = seed
        trace = Trace(
            identifier=ident,
            source=TraceSource.WEB_RESEARCH,
            fields={
                "tool": "fetch_url",
                "url": url,
                "status_code": resp.status_code,
                "title": title,
                "text_excerpt": text[:512] + ("…" if len(text) > 512 else ""),
            },
            evidence=evidence,
        )
        return ToolResult(
            content={
                "url": url,
                "status_code": resp.status_code,
                "title": title,
                "text": text,
                "evidence_sha256": evidence.payload_sha256,
            },
            trace=trace,
        )

    return [
        ToolSpec(
            name="web_search",
            description=(
                "Search the public web with DuckDuckGo. Use to find new "
                "candidate identifiers, mentions of the subject, profiles, "
                "or domains the existing collectors did not surface. "
                "Returns the top result URLs, titles, and snippets."
            ),
            schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (use quotes for exact phrases).",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 8,
                        "default": 5,
                        "description": "Max number of results to return (default 5).",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=web_search,
        ),
        ToolSpec(
            name="fetch_url",
            description=(
                "Fetch a public web page and return its title plus a "
                "readable text excerpt. Use to confirm a search hit, "
                "extract identifiers (emails, usernames, domains), or "
                "verify that a profile actually mentions the subject. "
                "Truncated to a few KB so the response fits in context."
            ),
            schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Absolute http(s) URL.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=fetch_url,
        ),
    ]


@contextlib.asynccontextmanager
async def _maybe_close(
    client: httpx.AsyncClient,
    *,
    owns: bool,
) -> AsyncIterator[httpx.AsyncClient]:
    """Close ``client`` after the block iff we constructed it ourselves.

    Tools that get an externally-owned client (``client_factory``
    provided by the caller) must not close it — that would break
    every subsequent tool call sharing the same connection pool.
    """
    try:
        yield client
    finally:
        if owns:
            with contextlib.suppress(Exception):
                await client.aclose()


# ---------------------------------------------------------------------------
# Tool runner
# ---------------------------------------------------------------------------


@dataclass
class ToolInvocation:
    """One round of (tool name, args, result, optional trace)."""

    name: str
    arguments: dict[str, Any]
    result: ToolResult


@dataclass
class ToolRunSummary:
    """Aggregated outcome of a research round.

    Carried back to the AgentLoop so it can both (a) inject the new
    traces into the working set and (b) record the tool transcript
    on the iteration step for the dossier UI.
    """

    invocations: tuple[ToolInvocation, ...] = ()
    new_traces: tuple[Trace, ...] = field(default_factory=tuple)
    over_budget: bool = False


async def run_tool(
    *,
    spec: ToolSpec,
    arguments: dict[str, Any],
    budget: ToolBudget,
) -> ToolResult:
    """Execute a single tool call subject to the iteration's budget.

    Decrements the budget *before* running the handler so a hung tool
    can't blow past ``calls_remaining``. Returns an error
    :class:`ToolResult` rather than raising on transport failure;
    the runner re-feeds that to the LLM as a normal tool message
    so it can decide whether to retry, switch tools, or give up.
    """
    if not budget.consume():
        return ToolResult(
            content={"error": "tool budget exhausted for this iteration"},
            error="budget",
        )
    try:
        return await asyncio.wait_for(
            spec.handler(arguments),
            timeout=budget.request_timeout * 2,
        )
    except TimeoutError:
        return ToolResult(
            content={"error": f"{spec.name} timed out"},
            error="timeout",
        )
    except Exception as exc:
        log.exception("tool %s raised: %s", spec.name, exc)
        return ToolResult(
            content={"error": f"{spec.name} failed: {exc}"},
            error=str(exc),
        )
