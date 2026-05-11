"""Doc-leak / public-document-share probe collector.

Searches public document hosting and paste sites for the seed identifier
to surface user-uploaded content that mentions a username or email. This
is the data-leak surface adjacent to HIBP (which only covers structured
breach corpora) — it catches documents accidentally uploaded to public
hosts that contain credentials, contact info, or other PII.

Sites probed:

Direct (no third-party search backend required):

- archive.org — Internet Archive full-text search (``advancedsearch.php``
  JSON endpoint; stable, deterministic, no anti-bot)
- pdfcoffee.com — PDF hosting (returns hit URLs in initial HTML)
- yumpu.com — magazine / flipbook hosting (returns hit URLs in HTML)
- pastebin.com — paste hosting via the public ``/u/<username>`` profile
  page (usernames only — pastebin has no free full-text search)

Via OpenAI Responses ``web_search`` tool (see
:mod:`reckora.reasoning.web_search`). These sites all gate their own
search behind a JavaScript SPA shell or a Cloudflare anti-bot
interstitial, so direct ``httpx.get`` returns zero hits even when the
platform clearly has the content. We delegate the search to OpenAI's
``web_search_preview`` tool (using whichever credential is configured —
Platform API key or ChatGPT OAuth) and anchor each returned URL against
the platform's canonical path regex.

- scribd.com — SPA shell on ``/search``
- slideshare.net — anti-bot interstitial
- issuu.com — SPA shell
- 4shared.com — search subdomain redirects to login wall
- dokumen.tips — Cloudflare 403 on ``/search/``
- calameo.com — 403 on ``/search``
- docplayer.net — anti-bot / regional DNS
- anyflip.com — no public ``/search`` endpoint

The ``pdfslide.net`` adapter was removed in this revision: the domain
now redirects to an ad-injecting third-party site
(``xoilaciiq.cc``) that no longer hosts the indexed PDFs, so emitting
traces against it produced false signal in dossiers.

For each site we emit one :class:`Trace`. The schema for ``Trace.fields``:

- ``platform`` — short site identifier (``"scribd"``, ``"pdfcoffee"``, …)
- ``query`` — canonicalised seed value used in the search query
- ``identifier_kind`` — ``"username"`` or ``"email"``
- ``search_url`` — the platform's *user-facing* search URL (for
  analyst click-through verification), even when the probe ran via
  the LLM web-search tool. The transport-level URL we actually fetched
  is captured in ``evidence_marker``.
- ``presence_status`` — one of:

  * ``"exists"`` — at least one hit URL was parsed out of the response
  * ``"not_found"`` — the server / search backend replied cleanly with
    zero hits
  * ``"blocked"`` — the server refused us (HTTP 4xx/5xx, transport
    error, or anti-bot interstitial); presence cannot be inferred
  * ``"unverified"`` — server replied 200 but we couldn't determine a
    hit count from the body (e.g. SPA shell, missing markers, or no
    web-search backend was configured for an SPA-only site)

- ``http_status`` — observed status code (``None`` on transport error /
  on web-search-routed sites where there is no per-platform HTTP call)
- ``hit_count`` — number of hit URLs parsed (capped at 50)
- ``hits`` — sample of up to 5 hits (``[{"url": ..., "title": ...}]``)
- ``evidence_marker`` — short triage string explaining why we picked
  the status (e.g. ``"scribd: 14 documents matched (via web_search)"``).

Like :class:`SocialPresenceProbeCollector`, we always emit one trace per
platform — even on ``not_found`` / ``blocked`` — so the dossier records
that the platform was *considered*. Analysts can filter on
``presence_status="exists"`` to see only confirmed hits.

The collector is feature-flagged behind the existing ``--breach`` /
``breach: true`` toggle. Stock investigations don't probe these sites,
matching the "best-effort, opt-in" contract used by HIBP.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, ClassVar
from urllib.parse import quote_plus

import httpx

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from ..reasoning.web_search import WebSearchError, WebSearchFn, WebSearchHit
from .base import Collector

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Conservative regex on the seed value. Sites accept all sorts of
# characters in queries, but we want to keep traffic deterministic and
# avoid sending shell-special / control bytes downstream.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,24}$")

# Per-platform regex of the "hit URL" pattern we look for in either
# the search-results body (direct probe) or the web-search citations
# (LLM-routed probe). Each pattern is anchored on the site's own
# canonical path shape so we only count URLs that genuinely live on
# that domain — ad/affiliate redirects coming back from the LLM tool
# get filtered out.
_HIT_PATTERNS: dict[str, re.Pattern[str]] = {
    "scribd": re.compile(
        r"https?://(?:www\.)?scribd\.com/(?:document|presentation)/\d+/[A-Za-z0-9_\-]+"
    ),
    "pdfcoffee": re.compile(r"https?://pdfcoffee\.com/[A-Za-z0-9_\-]+\.html"),
    "slideshare": re.compile(
        r"https?://(?:www\.)?slideshare\.net/(?:slideshow/)?[A-Za-z0-9_\-]+/[A-Za-z0-9_\-]+"
    ),
    "issuu": re.compile(r"https?://issuu\.com/[A-Za-z0-9_\-]+/docs/[A-Za-z0-9_\-]+"),
    "4shared": re.compile(
        r"https?://(?:www\.)?4shared\.com/(?:document|file|account|web|s)/[A-Za-z0-9_\-/]+\.html"
    ),
    "pastebin": re.compile(r"https?://pastebin\.com/[A-Za-z0-9]{8}\b"),
    "yumpu": re.compile(
        r"https?://(?:www\.)?yumpu\.com/[a-z]{2}/document/(?:view|read)/\d+/[A-Za-z0-9_\-]+"
    ),
    "calameo": re.compile(r"https?://(?:[a-z]{2}\.|www\.)?calameo\.com/(?:books|read)/[0-9a-f]+"),
    "docplayer": re.compile(r"https?://docplayer\.net/\d+-[A-Za-z0-9_\-]+\.html"),
    "dokumen_tips": re.compile(r"https?://dokumen\.tips/documents/[A-Za-z0-9_\-]+\.html"),
    "anyflip": re.compile(r"https?://anyflip\.com/[A-Za-z0-9]{4,8}/[A-Za-z0-9]{4,8}/?"),
}

# Per-platform extractor for a short title near the hit URL. Best-effort:
# we look for `<a ... href="<url>"...>title</a>` in the surrounding HTML.
# Falls back to "" when no anchor text is found, which is fine — the URL
# itself is the high-signal field.
_ANCHOR_RE = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>([^<]{1,200})</a>", re.IGNORECASE)

# Maximum number of hits we surface per platform. Picked to keep dossier
# JSON payloads bounded — analysts who need more can click through.
_MAX_HITS = 5

# Cap on how many citations to request from the LLM web-search backend
# per platform. Larger than ``_MAX_HITS`` so the per-domain regex has
# room to filter ad / affiliate URLs without starving the final list.
_WEB_SEARCH_LIMIT = 12

# Sites whose user-facing search URL is recorded as ``search_url`` for
# analysts. The keys are the platform tokens used in the trace; the
# values are format strings that take a single positional argument
# (the URL-encoded query). These URLs are *not* fetched directly when
# the collector routes via web search — they're surfaced so an analyst
# can re-run the search in a browser and confirm the LLM-routed hits.
_PLATFORM_SEARCH_URL: dict[str, str] = {
    "scribd": "https://www.scribd.com/search?query={0}",
    "slideshare": "https://www.slideshare.net/search?q={0}",
    "issuu": "https://issuu.com/search?q={0}",
    "4shared": "https://search.4shared.com/q/CCAD/1/{0}",
    "calameo": "https://www.calameo.com/search?q={0}",
    "docplayer": "https://docplayer.net/search/?q={0}",
    "dokumen_tips": "https://dokumen.tips/search/?q={0}",
    "anyflip": "https://anyflip.com/search?q={0}",
}

# Direct-probe URL builders. These run a plain ``httpx.get`` and parse
# the body with the per-platform regex.
_DIRECT_PROBE_URL: dict[str, str] = {
    "pdfcoffee": "https://pdfcoffee.com/?s={0}",
    "yumpu": "https://www.yumpu.com/en/search?q={0}",
    "pastebin": "https://pastebin.com/u/{0}",
}


class DocLeakCollector(Collector):
    """Probe public doc-share / paste sites for mentions of an identifier.

    Parameters
    ----------
    client:
        Optional shared :class:`httpx.AsyncClient` for tests / connection
        reuse. Falls back to a per-call client if not provided.
    timeout:
        Per-site timeout in seconds. Each adapter has the same budget
        and they fan out concurrently via ``asyncio.gather``.
    web_search_fn:
        Optional :data:`WebSearchFn` used to query SPA / anti-bot
        platforms via OpenAI's ``web_search_preview`` tool. When unset
        those platforms emit an ``unverified`` trace explaining the
        missing backend; direct-probe platforms (archive.org,
        pdfcoffee, yumpu, pastebin) keep working without it.
    """

    name: ClassVar[str] = "doc_leak"
    supported: ClassVar[frozenset[str]] = frozenset(
        {IdentifierType.USERNAME.value, IdentifierType.EMAIL.value}
    )

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        timeout: float = 12.0,
        web_search_fn: WebSearchFn | None = None,
    ) -> None:
        super().__init__(client)
        self._timeout = timeout
        self._web_search_fn = web_search_fn

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        kind = identifier.type.value
        query = identifier.value.strip()
        if kind == IdentifierType.USERNAME.value:
            query = query.lstrip("@")
            if not _USERNAME_RE.match(query):
                return []
        elif kind == IdentifierType.EMAIL.value:
            query = query.lower()
            if not _EMAIL_RE.match(query):
                return []
        else:  # pragma: no cover — supports() guards this
            return []

        client = await self._http()

        # All adapters share the same shape:
        # async def adapter(client, query, kind) -> AdapterResult
        # We fan them out, then materialise traces from the results.
        adapters: list[Any] = [
            self._probe_archive_org,
            self._probe_pdfcoffee,
            self._probe_yumpu,
            self._probe_scribd,
            self._probe_slideshare,
            self._probe_issuu,
            self._probe_4shared,
            self._probe_calameo,
            self._probe_docplayer,
            self._probe_dokumen_tips,
            self._probe_anyflip,
        ]
        # Pastebin only has a useful per-site lookup for usernames (their
        # profile page); skip it for email queries.
        if kind == IdentifierType.USERNAME.value:
            adapters.append(self._probe_pastebin)

        results = await asyncio.gather(
            *(adapter(client, query, kind) for adapter in adapters),
            return_exceptions=False,
        )

        traces: list[Trace] = []
        for fields, source_url, evidence_payload in results:
            if fields is None:
                continue
            traces.append(
                Trace(
                    identifier=identifier,
                    source=TraceSource.DOC_LEAK,
                    fields=fields,
                    evidence=make_evidence(source_url, evidence_payload, keep_raw=False),
                ),
            )
        return traces

    # ---------------------------------------------------------------- direct probes

    async def _probe_pdfcoffee(
        self, client: httpx.AsyncClient, query: str, kind: str
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        url = _DIRECT_PROBE_URL["pdfcoffee"].format(quote_plus(query))
        return await self._html_search(client, url, query=query, kind=kind, platform="pdfcoffee")

    async def _probe_yumpu(
        self, client: httpx.AsyncClient, query: str, kind: str
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        url = _DIRECT_PROBE_URL["yumpu"].format(quote_plus(query))
        return await self._html_search(client, url, query=query, kind=kind, platform="yumpu")

    async def _probe_pastebin(
        self, client: httpx.AsyncClient, query: str, kind: str
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        # Pastebin doesn't expose a free per-site full-text search, but
        # the public ``/u/<username>`` profile reveals whether a user
        # account exists and lists recent public pastes — a useful
        # surrogate for "this username has a known paste history".
        url = _DIRECT_PROBE_URL["pastebin"].format(quote_plus(query))
        return await self._html_search(client, url, query=query, kind=kind, platform="pastebin")

    async def _probe_archive_org(
        self, client: httpx.AsyncClient, query: str, kind: str
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        """Internet Archive full-text search via the JSON endpoint.

        We use ``advancedsearch.php?output=json`` rather than scraping the
        HTML SERP because it returns a deterministic ``response.docs``
        array — easier to parse and harder to break than HTML.
        """
        url = (
            "https://archive.org/advancedsearch.php"
            f"?q={quote_plus(query)}&fl[]=identifier&fl[]=title&fl[]=mediatype"
            "&rows=10&page=1&output=json"
        )
        platform = "archive_org"
        try:
            resp = await client.get(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": _BROWSER_UA,
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            return (
                _make_fields(
                    platform=platform,
                    query=query,
                    kind=kind,
                    search_url=url,
                    presence_status="blocked",
                    http_status=None,
                    hit_count=0,
                    hits=[],
                    evidence_marker=f"transport error: {type(exc).__name__}",
                ),
                url,
                {"error": type(exc).__name__},
            )
        status = resp.status_code
        evidence_payload: dict[str, Any] = {"http_status": status}
        if status >= 400:
            return (
                _make_fields(
                    platform=platform,
                    query=query,
                    kind=kind,
                    search_url=url,
                    presence_status="blocked",
                    http_status=status,
                    hit_count=0,
                    hits=[],
                    evidence_marker=f"archive.org: HTTP {status}",
                ),
                url,
                evidence_payload,
            )
        try:
            data = resp.json()
        except ValueError:
            return (
                _make_fields(
                    platform=platform,
                    query=query,
                    kind=kind,
                    search_url=url,
                    presence_status="unverified",
                    http_status=status,
                    hit_count=0,
                    hits=[],
                    evidence_marker="archive.org: 200 with non-JSON body",
                ),
                url,
                evidence_payload,
            )
        response = _safe_dict(data).get("response")
        docs_raw = _safe_dict(response).get("docs")
        docs = docs_raw if isinstance(docs_raw, list) else []
        # ``response.numFound`` is the authoritative total; ``docs`` is
        # capped by ``rows``. We keep both — total for triage, sample
        # for the dossier.
        num_found_raw = _safe_dict(response).get("numFound")
        num_found = num_found_raw if isinstance(num_found_raw, int) else len(docs)
        hits: list[dict[str, str]] = []
        for doc in docs[:_MAX_HITS]:
            if not isinstance(doc, dict):
                continue
            ident = doc.get("identifier")
            title = doc.get("title")
            if isinstance(ident, str) and ident:
                hit_url = f"https://archive.org/details/{ident}"
                hits.append(
                    {
                        "url": hit_url,
                        "title": title if isinstance(title, str) else "",
                    }
                )
        evidence_payload["num_found"] = num_found
        evidence_payload["sample_size"] = len(hits)
        if num_found <= 0:
            return (
                _make_fields(
                    platform=platform,
                    query=query,
                    kind=kind,
                    search_url=url,
                    presence_status="not_found",
                    http_status=status,
                    hit_count=0,
                    hits=[],
                    evidence_marker="archive.org: 0 results",
                ),
                url,
                evidence_payload,
            )
        return (
            _make_fields(
                platform=platform,
                query=query,
                kind=kind,
                search_url=url,
                presence_status="exists",
                http_status=status,
                hit_count=num_found,
                hits=hits,
                evidence_marker=f"archive.org: {num_found} matches (showing {len(hits)})",
            ),
            url,
            evidence_payload,
        )

    # ---------------------------------------------------------------- web-search probes

    async def _probe_scribd(
        self, client: httpx.AsyncClient, query: str, kind: str
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        return await self._web_search_probe(query=query, kind=kind, platform="scribd")

    async def _probe_slideshare(
        self, client: httpx.AsyncClient, query: str, kind: str
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        return await self._web_search_probe(query=query, kind=kind, platform="slideshare")

    async def _probe_issuu(
        self, client: httpx.AsyncClient, query: str, kind: str
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        return await self._web_search_probe(query=query, kind=kind, platform="issuu")

    async def _probe_4shared(
        self, client: httpx.AsyncClient, query: str, kind: str
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        return await self._web_search_probe(query=query, kind=kind, platform="4shared")

    async def _probe_calameo(
        self, client: httpx.AsyncClient, query: str, kind: str
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        return await self._web_search_probe(query=query, kind=kind, platform="calameo")

    async def _probe_docplayer(
        self, client: httpx.AsyncClient, query: str, kind: str
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        return await self._web_search_probe(query=query, kind=kind, platform="docplayer")

    async def _probe_dokumen_tips(
        self, client: httpx.AsyncClient, query: str, kind: str
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        return await self._web_search_probe(query=query, kind=kind, platform="dokumen_tips")

    async def _probe_anyflip(
        self, client: httpx.AsyncClient, query: str, kind: str
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        return await self._web_search_probe(query=query, kind=kind, platform="anyflip")

    # --------------------------------------------------------------- shared

    async def _web_search_probe(
        self,
        *,
        query: str,
        kind: str,
        platform: str,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        """Run ``site:<domain> "<query>"`` via the LLM web-search tool.

        Validates returned URL citations against the platform's
        :data:`_HIT_PATTERNS` regex so we only count URLs that genuinely
        live on the target domain. When no web-search backend is wired
        up (``web_search_fn is None``) we emit an ``unverified`` trace
        rather than fail the whole collector — the trace records that
        the platform was considered and explains why it wasn't probed.
        """
        canonical = _platform_search_url(platform, query)
        evidence_url = canonical or f"web_search://{platform}?q={quote_plus(query)}"
        if self._web_search_fn is None:
            return (
                _make_fields(
                    platform=platform,
                    query=query,
                    kind=kind,
                    search_url=canonical,
                    presence_status="unverified",
                    http_status=None,
                    hit_count=0,
                    hits=[],
                    evidence_marker=f"{platform}: no web-search backend configured",
                ),
                evidence_url,
                {"backend": "none"},
            )

        domain = _platform_domain(platform)
        dorked = f'site:{domain} "{query}"'
        try:
            citations = await self._web_search_fn(dorked)
        except WebSearchError as exc:
            return (
                _make_fields(
                    platform=platform,
                    query=query,
                    kind=kind,
                    search_url=canonical,
                    presence_status="blocked",
                    http_status=None,
                    hit_count=0,
                    hits=[],
                    evidence_marker=f"{platform}: web_search backend error ({exc})",
                ),
                evidence_url,
                {"backend": "web_search", "error": str(exc)},
            )

        hits = _filter_citations(citations, platform=platform)
        evidence_payload: dict[str, Any] = {
            "backend": "web_search",
            "raw_citations": len(citations),
            "matched_citations": len(hits),
        }
        if not hits:
            return (
                _make_fields(
                    platform=platform,
                    query=query,
                    kind=kind,
                    search_url=canonical,
                    presence_status="not_found",
                    http_status=None,
                    hit_count=0,
                    hits=[],
                    evidence_marker=(
                        f"{platform}: web_search returned {len(citations)} citation(s), "
                        "none matched the platform URL shape"
                    ),
                ),
                evidence_url,
                evidence_payload,
            )
        return (
            _make_fields(
                platform=platform,
                query=query,
                kind=kind,
                search_url=canonical,
                presence_status="exists",
                http_status=None,
                hit_count=len(hits),
                hits=hits[:_MAX_HITS],
                evidence_marker=f"{platform}: {len(hits)} hit URLs from web_search",
            ),
            evidence_url,
            evidence_payload,
        )

    async def _html_search(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        query: str,
        kind: str,
        platform: str,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        """Fetch a search-results HTML page and count platform-specific hits.

        The shape is identical for every HTML site — only the hit URL
        regex differs (looked up in :data:`_HIT_PATTERNS`). Anti-bot
        responses (4xx/5xx, transport errors, Cloudflare interstitials)
        produce a ``"blocked"`` trace; clean 200s with zero hits produce
        ``"not_found"``; clean 200s with hits produce ``"exists"``.
        """
        try:
            resp = await client.get(
                url,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                    "User-Agent": _BROWSER_UA,
                },
                timeout=self._timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            return (
                _make_fields(
                    platform=platform,
                    query=query,
                    kind=kind,
                    search_url=url,
                    presence_status="blocked",
                    http_status=None,
                    hit_count=0,
                    hits=[],
                    evidence_marker=f"transport error: {type(exc).__name__}",
                ),
                url,
                {"error": type(exc).__name__},
            )

        status = resp.status_code
        evidence_payload: dict[str, Any] = {"http_status": status}
        if status >= 400:
            return (
                _make_fields(
                    platform=platform,
                    query=query,
                    kind=kind,
                    search_url=url,
                    presence_status="blocked",
                    http_status=status,
                    hit_count=0,
                    hits=[],
                    evidence_marker=f"{platform}: HTTP {status}",
                ),
                url,
                evidence_payload,
            )

        body = resp.text
        body_low = body.lower()
        # Very short bodies tend to be SPA shells / login walls — flag as
        # unverified rather than risk a false ``not_found``.
        if len(body) < 256:
            return (
                _make_fields(
                    platform=platform,
                    query=query,
                    kind=kind,
                    search_url=url,
                    presence_status="unverified",
                    http_status=status,
                    hit_count=0,
                    hits=[],
                    evidence_marker=f"{platform}: 200 with body of {len(body)}b",
                ),
                url,
                evidence_payload,
            )
        # Detect Cloudflare / generic anti-bot interstitials. These
        # commonly return 200 with a "checking your browser" page; we
        # don't want to count zero hits on those as ``not_found``.
        if (
            "checking your browser" in body_low
            or "attention required! | cloudflare" in body_low
            or ("captcha" in body_low and "<form" in body_low)
        ):
            return (
                _make_fields(
                    platform=platform,
                    query=query,
                    kind=kind,
                    search_url=url,
                    presence_status="blocked",
                    http_status=status,
                    hit_count=0,
                    hits=[],
                    evidence_marker=f"{platform}: anti-bot interstitial",
                ),
                url,
                evidence_payload,
            )

        hits = _parse_hits(body, platform=platform)
        if not hits:
            return (
                _make_fields(
                    platform=platform,
                    query=query,
                    kind=kind,
                    search_url=url,
                    presence_status="not_found",
                    http_status=status,
                    hit_count=0,
                    hits=[],
                    evidence_marker=f"{platform}: 0 results",
                ),
                url,
                evidence_payload,
            )
        evidence_payload["sample_size"] = len(hits)
        return (
            _make_fields(
                platform=platform,
                query=query,
                kind=kind,
                search_url=url,
                presence_status="exists",
                http_status=status,
                hit_count=len(hits),
                hits=hits[:_MAX_HITS],
                evidence_marker=f"{platform}: {len(hits)} hit URLs parsed",
            ),
            url,
            evidence_payload,
        )


def _platform_search_url(platform: str, query: str) -> str:
    """Render the user-facing search URL recorded as ``search_url``.

    Falls back to the empty string for platforms we don't have a known
    canonical search URL for — the trace's ``evidence_marker`` and
    ``hits[].url`` still carry actionable information in that case.
    """
    template = _PLATFORM_SEARCH_URL.get(platform)
    return template.format(quote_plus(query)) if template else ""


def _platform_domain(platform: str) -> str:
    """Map a platform token to the domain used in ``site:`` dorks."""
    return {
        "scribd": "scribd.com",
        "slideshare": "slideshare.net",
        "issuu": "issuu.com",
        "4shared": "4shared.com",
        "calameo": "calameo.com",
        "docplayer": "docplayer.net",
        "dokumen_tips": "dokumen.tips",
        "anyflip": "anyflip.com",
    }.get(platform, platform)


def _filter_citations(
    citations: list[WebSearchHit],
    *,
    platform: str,
) -> list[dict[str, str]]:
    """Keep only URLs that match :data:`_HIT_PATTERNS` for ``platform``.

    LLM web-search tools sometimes return outbound URLs (ad redirects,
    cached-page mirrors, archive.org snapshots) even with a strict
    ``site:`` operator. Anchoring against the per-platform regex
    guarantees the surfaced hits are genuinely from the target domain.
    De-duplicates on URL.
    """
    hit_re = _HIT_PATTERNS.get(platform)
    if hit_re is None:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for citation in citations:
        if hit_re.fullmatch(citation.url) is None:
            continue
        if citation.url in seen:
            continue
        seen.add(citation.url)
        title = citation.title or citation.snippet
        out.append({"url": citation.url, "title": title[:200] if title else ""})
    return out


def _parse_hits(body: str, *, platform: str) -> list[dict[str, str]]:
    """Extract hit URLs (and best-effort titles) from a search-results body.

    Uses the platform-specific :data:`_HIT_PATTERNS` regex to find
    canonical content URLs, then opportunistically pulls anchor text
    from the surrounding ``<a href=...>`` tag for a human-readable
    title. De-duplicates by URL.
    """
    hit_re = _HIT_PATTERNS.get(platform)
    if hit_re is None:
        return []
    matches = hit_re.findall(body)
    if not matches:
        return []

    # Build a URL->title map by scanning anchors in document order so
    # we associate the closest title with each hit URL.
    titles: dict[str, str] = {}
    for href, text in _ANCHOR_RE.findall(body):
        if href in titles:
            continue
        # Only keep titles whose href matches one of the hit URLs we
        # care about (avoids polluting with unrelated nav/footer links).
        if hit_re.fullmatch(href) is not None:
            stripped = re.sub(r"\s+", " ", text).strip()
            if stripped:
                titles[href] = stripped[:200]

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for url in matches:
        if url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "title": titles.get(url, "")})
    return out


def _make_fields(
    *,
    platform: str,
    query: str,
    kind: str,
    search_url: str,
    presence_status: str,
    http_status: int | None,
    hit_count: int,
    hits: list[dict[str, str]],
    evidence_marker: str,
) -> dict[str, Any]:
    return {
        "platform": platform,
        "query": query,
        "identifier_kind": kind,
        "search_url": search_url,
        "presence_status": presence_status,
        "http_status": http_status,
        "hit_count": hit_count,
        "hits": hits,
        "evidence_marker": evidence_marker,
    }


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


__all__ = ["_WEB_SEARCH_LIMIT", "DocLeakCollector"]
