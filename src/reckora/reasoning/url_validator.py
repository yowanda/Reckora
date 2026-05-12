"""Lightweight URL validation for leak / doc-share collector hits.

Two failure modes plague the URLs returned by
:mod:`reckora.reasoning.web_search` and
:mod:`reckora.collectors.doc_leak`'s direct-probe path:

* **Hallucinated URLs.** The Codex OAuth backend's ``web_search`` tool
  is grounded by Bing's index, but the *model's reproduction* of the
  result URLs into the assistant message text occasionally drops a
  slug character or invents a plausible-but-non-existent slug. Those
  URLs match the per-platform canonical regex (``scribd.com/document/
  \\d+/[A-Za-z0-9_-]+``) so the doc-leak collector accepts them, but a
  ``HEAD`` request returns ``404`` because the document was never
  indexed in the first place.
* **Substring-matching noise from direct probes.** ``pdfcoffee`` and
  ``yumpu`` HTML search-results pages return URLs whose slug contains
  the seed as a substring (``...elonmusk-pdf-free.html``) even when
  the underlying document is entirely unrelated to the target — the
  same kind of false positive that motivated the rest of the
  per-site regex anchoring.

This module provides:

* :func:`probe_urls` — issue parallel ``HEAD`` requests (with a small
  in-flight concurrency cap) and return a status verdict for each
  URL. ``HEAD`` is preferred because it doesn't transfer body bytes
  and most CDNs honour it cheaply. ``GET`` is retried automatically
  when the server returns ``HTTP 405 Method Not Allowed`` (a common
  Cloudflare default).
* :func:`verify_seed_in_body` — for direct-probe hits where URL
  matching alone is too permissive, fetch a small body sample and
  check whether the seed identifier actually appears in the page
  title, meta description, or visible text. Returns ``None`` for
  transport errors (so the caller can fall back to URL-only signal)
  and a boolean otherwise.

Both helpers are deliberately small and synchronous-friendly — they
don't import any project-internal modules other than ``httpx`` so
they can be reused from any collector without circular-import risk.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from dataclasses import dataclass

import httpx

# Concurrency cap on parallel HEAD requests. Eight is a sweet spot:
# big enough to keep the per-investigation tail latency low (typical
# CDN HEAD round-trip is ~150-400 ms), small enough that bursty
# probes don't get rate-limited by the upstream site itself.
DEFAULT_PROBE_CONCURRENCY = 8

# Per-URL timeout. CDN HEADs are fast but origin servers behind a
# slow database (scribd, slideshare under load) occasionally take
# 5-7 seconds. Eight seconds keeps the worst case bounded without
# false-negatives on healthy-but-slow origins.
DEFAULT_PROBE_TIMEOUT = 8.0

# How much of the response body to read when verifying seed presence.
# 64 KiB is enough to cover the ``<head>`` (title, meta-description,
# OpenGraph tags) and the opening paragraphs of the visible content
# on virtually every paste / doc-share platform. Reading the full
# body would expose us to multi-MB PDFs / videos and offers no
# additional signal.
_VERIFY_BODY_BYTES = 64 * 1024

# HTTP status codes that count as "the resource exists":
# - 2xx: ordinary success
# - 3xx: any redirect (we follow them via ``follow_redirects=True``,
#   so the only 3xx that reach us are loops / refused redirects —
#   still indicates the URL is real)
# - 401 / 403: explicit auth wall. The document does exist on the
#   platform, the platform just refuses to show it to us without a
#   logged-in session. From an OSINT standpoint that's still a
#   confirmed presence signal — analysts log in manually to inspect.
# - 405: Method Not Allowed on HEAD. We retry as GET so this code
#   should never reach the verdict stage; documented here for
#   completeness.
_ALIVE_STATUSES: frozenset[int] = frozenset({401, 403})

# Status codes that unambiguously mean "the resource does NOT exist".
# 404/410 are the strong signals; we treat everything else (5xx,
# 429, transport timeouts) as ``None`` so the caller can decide
# whether to retain the URL pending a retry vs drop it outright.
_DEAD_STATUSES: frozenset[int] = frozenset({404, 410})

# Browser-y User-Agent. A bare ``python-httpx`` UA gets 403'd by
# Cloudflare on slideshare, scribd, dokumen.tips before the HEAD
# even reaches the origin — masquerading as Chrome avoids the
# anti-bot interstitial for ordinary HEAD probes.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class URLProbeResult:
    """Verdict for one ``HEAD``-probed URL.

    ``alive`` is the boolean view the collector usually cares about
    (``True`` = keep the hit, ``False`` = drop as fake/dead). The
    other fields are kept for diagnostics so an analyst can tell
    "we dropped this because it 404'd" apart from "we dropped this
    because the request timed out".
    """

    url: str
    alive: bool
    http_status: int | None
    final_url: str | None  # post-redirect URL when ``alive``; ``None`` otherwise
    error: str | None  # ``type(exc).__name__`` for transport errors


async def probe_urls(
    urls: Iterable[str],
    *,
    client: httpx.AsyncClient,
    concurrency: int = DEFAULT_PROBE_CONCURRENCY,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> list[URLProbeResult]:
    """Run ``HEAD`` probes against ``urls`` in parallel.

    Returns one :class:`URLProbeResult` per input URL, in the same
    order. Duplicates in the input list are de-duplicated before
    probing (a side benefit of using a dict to cache verdicts), then
    the original order is restored on the way out.

    Concurrency is bounded by an :class:`asyncio.Semaphore` so a
    page with 50 hits doesn't trigger 50 simultaneous HEADs — most
    sites tolerate 8 in-flight from one IP cheerfully, more starts
    looking like an attack.
    """
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)

    if not deduped:
        return []

    semaphore = asyncio.Semaphore(concurrency)

    async def probe_one(url: str) -> URLProbeResult:
        async with semaphore:
            return await _probe_single(url, client=client, timeout=timeout)

    results = await asyncio.gather(*(probe_one(u) for u in deduped))
    return list(results)


async def _probe_single(
    url: str,
    *,
    client: httpx.AsyncClient,
    timeout: float,
) -> URLProbeResult:
    """Single-URL HEAD probe with automatic GET fallback on 405."""
    try:
        resp = await client.head(
            url,
            headers={"User-Agent": _BROWSER_UA},
            timeout=timeout,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        return URLProbeResult(
            url=url,
            alive=False,
            http_status=None,
            final_url=None,
            error=type(exc).__name__,
        )

    status = resp.status_code
    if status == 405:
        # Some CDNs disable HEAD; fall back to a single-byte GET
        # using a ``Range`` header to avoid streaming the full body.
        try:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": _BROWSER_UA,
                    "Range": "bytes=0-0",
                },
                timeout=timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            return URLProbeResult(
                url=url,
                alive=False,
                http_status=None,
                final_url=None,
                error=type(exc).__name__,
            )
        status = resp.status_code

    final_url = str(resp.url) if resp.url is not None else None
    if status in _DEAD_STATUSES:
        return URLProbeResult(
            url=url,
            alive=False,
            http_status=status,
            final_url=final_url,
            error=None,
        )
    alive = 200 <= status < 400 or status in _ALIVE_STATUSES
    return URLProbeResult(
        url=url,
        alive=alive,
        http_status=status,
        final_url=final_url if alive else None,
        error=None,
    )


# ``<title>...</title>`` capture, case-insensitive, non-greedy. Used by
# :func:`verify_seed_in_body` so we can score title matches higher than
# random body-text matches (a seed appearing in a doc title is a much
# stronger relevance signal than a fleeting mention in the body).
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# OpenGraph / meta-description capture. Same intuition: meta tags are
# author-controlled and tend to mention the actual subject, while
# random body text on a long page often coincidentally contains the
# seed as a substring of an unrelated word.
_META_RE = re.compile(
    r'<meta\s+(?:property|name)=["\']?(?:og:title|og:description|description|twitter:title)["\']?'
    r'\s+content=["\']([^"\']{0,512})["\']',
    re.IGNORECASE,
)


async def verify_seed_in_body(
    url: str,
    seed: str,
    *,
    client: httpx.AsyncClient,
    body_bytes: int = _VERIFY_BODY_BYTES,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> bool | None:
    """Fetch a small body sample of ``url`` and check for ``seed``.

    Returns:

    * ``True`` if ``seed`` (case-insensitively) appears in the page
      ``<title>``, an ``og:*`` / ``description`` meta tag, or the
      first ``body_bytes`` of the body text.
    * ``False`` if the page loaded cleanly but ``seed`` is absent —
      strong signal that the URL is noise (search-result page with a
      tangentially matching slug, ad redirect, etc.).
    * ``None`` on any transport error or non-2xx response — the
      caller should fall back to URL-only signal because we couldn't
      make a confident determination either way.

    The function is intentionally tolerant of HTML noise: we lower-case
    the entire sample once and substring-match the seed, which catches
    seeds embedded in attribute values, JSON-LD blobs, comments, and
    server-rendered SPA bootstrap data without parsing the page.
    """
    seed_norm = seed.lower().strip()
    if not seed_norm:
        return None

    try:
        resp = await client.get(
            url,
            headers={
                "User-Agent": _BROWSER_UA,
                "Range": f"bytes=0-{body_bytes - 1}",
            },
            timeout=timeout,
            follow_redirects=True,
        )
    except httpx.HTTPError:
        return None
    if resp.status_code >= 400:
        return None

    body = resp.text[:body_bytes]
    body_low = body.lower()
    if seed_norm in body_low:
        return True
    title_match = _TITLE_RE.search(body)
    if title_match and seed_norm in title_match.group(1).lower():
        return True
    return any(seed_norm in meta.group(1).lower() for meta in _META_RE.finditer(body))
