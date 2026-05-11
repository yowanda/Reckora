"""Tests for the DocLeakCollector.

The collector probes 12 public document-share / paste sites in parallel
and emits one Trace per site. Four of the sites (archive.org,
pdfcoffee, yumpu, pastebin) are probed directly with ``httpx`` and
mocked here via ``pytest-httpx``. The remaining eight (scribd,
slideshare, issuu, 4shared, calameo, docplayer, dokumen.tips, anyflip)
go through the injected :data:`WebSearchFn` because their own search
endpoints serve SPA shells or anti-bot interstitials in production; we
inject a deterministic fake search function to exercise that path.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

import httpx
import pytest
from pytest_httpx import HTTPXMock

from reckora.collectors.doc_leak import DocLeakCollector
from reckora.models.entity import Identifier, Trace
from reckora.models.enums import IdentifierType, TraceSource
from reckora.reasoning.web_search import WebSearchError, WebSearchHit


@pytest.fixture
def user_alice() -> Identifier:
    return Identifier(type=IdentifierType.USERNAME, value="alice")


@pytest.fixture
def email_alice() -> Identifier:
    return Identifier(type=IdentifierType.EMAIL, value="alice@example.com")


# --------------------------------------------------------------- URL helpers


def _pdfcoffee(q: str) -> str:
    return f"https://pdfcoffee.com/?s={quote_plus(q)}"


def _yumpu(q: str) -> str:
    return f"https://www.yumpu.com/en/search?q={quote_plus(q)}"


def _pastebin(q: str) -> str:
    return f"https://pastebin.com/u/{quote_plus(q)}"


def _archive(q: str) -> str:
    return (
        "https://archive.org/advancedsearch.php"
        f"?q={quote_plus(q)}&fl[]=identifier&fl[]=title&fl[]=mediatype"
        "&rows=10&page=1&output=json"
    )


def _by_platform(traces: list[Trace]) -> dict[str, dict[str, Any]]:
    return {trace.fields["platform"]: trace.fields for trace in traces}


# Web-search platforms — the eight sites we delegate to the LLM tool.
_WEB_SEARCH_PLATFORMS = (
    "scribd",
    "slideshare",
    "issuu",
    "4shared",
    "calameo",
    "docplayer",
    "dokumen_tips",
    "anyflip",
)


# ----------------------------------------------------------------- fixtures


# Padded out past the 256-byte threshold so the short-body branch
# doesn't fire on hit fixtures.
_PDFCOFFEE_HITS = (
    "<html><body>"
    + '<article><a href="https://pdfcoffee.com/alice-leak-2023-pdf-free.html">'
    + "Alice leak 2023</a></article>"
    + '<article><a href="https://pdfcoffee.com/alice-bio-pdf-free.html">'
    + "Alice bio</a></article>"
    + ("<p>filler</p>" * 20)
    + "</body></html>"
)

_YUMPU_HITS = (
    "<html><body>"
    + '<a href="https://www.yumpu.com/en/document/view/12345678/alice-report-2024">'
    + "Alice report 2024</a>"
    + '<a href="https://www.yumpu.com/en/document/read/87654321/alice-newsletter">'
    + "Alice newsletter</a>"
    + ("<p>filler</p>" * 20)
    + "</body></html>"
)

_PASTEBIN_USER_PAGE = (
    "<html><body>"
    '<table class="maintable">'
    '<tr><td><a href="https://pastebin.com/AbCdEfGh">paste1</a></td></tr>'
    '<tr><td><a href="https://pastebin.com/12345678">paste2</a></td></tr>'
    "</table>" + ("<p>filler</p>" * 20) + "</body></html>"
)

_PASTEBIN_NO_USER = "<html><body>Unknown user</body></html>" + ("x" * 300)

_EMPTY_BODY = "<html><body><p>No results found</p></body></html>" + ("x" * 300)

_ARCHIVE_HITS_JSON = {
    "response": {
        "numFound": 4,
        "start": 0,
        "docs": [
            {"identifier": "alice-doc-2020", "title": "Alice doc 2020", "mediatype": "texts"},
            {"identifier": "alice-archive", "title": "Alice archive", "mediatype": "texts"},
        ],
    }
}

_ARCHIVE_NO_HITS_JSON = {"response": {"numFound": 0, "start": 0, "docs": []}}

# Deterministic per-platform citation URLs the fake WebSearchFn returns.
# Each URL is shaped to match the platform's ``_HIT_PATTERNS`` regex so
# the filter accepts them; the second entry on each platform is an
# off-domain citation that the regex MUST reject.
_FAKE_WEB_SEARCH_HITS: dict[str, list[WebSearchHit]] = {
    "scribd": [
        WebSearchHit(
            url="https://www.scribd.com/document/123456/Alice-Resume",
            title="Alice's resume PDF",
        ),
        WebSearchHit(url="https://example.com/ad?ref=scribd", title="ad redirect"),
    ],
    "slideshare": [
        WebSearchHit(
            url="https://www.slideshare.net/alice/credentials-2021",
            title="Alice credentials deck",
        ),
    ],
    "issuu": [
        WebSearchHit(
            url="https://issuu.com/alice/docs/private-notes",
            title="Alice private notes",
        ),
    ],
    "4shared": [
        WebSearchHit(
            url="https://www.4shared.com/document/abc12345/alice-leak.html",
            title="Alice leak",
        ),
    ],
    "calameo": [
        WebSearchHit(
            url="https://en.calameo.com/books/000123456abcdef0123",
            title="Alice flipbook",
        ),
    ],
    "docplayer": [
        WebSearchHit(
            url="https://docplayer.net/12345678-Alice-Annual-Report.html",
            title="Alice annual report",
        ),
    ],
    "dokumen_tips": [
        WebSearchHit(
            url="https://dokumen.tips/documents/alice-internal-2023.html",
            title="Alice internal 2023",
        ),
    ],
    "anyflip": [
        WebSearchHit(
            url="https://anyflip.com/abcd/efgh/",
            title="Alice anyflip catalog",
        ),
    ],
}


# ------------------------------------------------------- fake WebSearch helper


def _make_fake_web_search(
    *,
    hits_by_platform: dict[str, list[WebSearchHit]] | None = None,
    fail_with: type[BaseException] | None = None,
) -> Any:
    """Return a fake ``WebSearchFn`` that maps a ``site:`` dork to fixtures.

    The collector queries ``site:<domain> "<value>"``; the helper finds
    the platform whose domain appears in the query and returns the
    matching fixture (or an empty list, or raises, depending on the
    test's needs).
    """
    table = hits_by_platform if hits_by_platform is not None else _FAKE_WEB_SEARCH_HITS
    domain_to_platform = {
        "scribd.com": "scribd",
        "slideshare.net": "slideshare",
        "issuu.com": "issuu",
        "4shared.com": "4shared",
        "calameo.com": "calameo",
        "docplayer.net": "docplayer",
        "dokumen.tips": "dokumen_tips",
        "anyflip.com": "anyflip",
    }

    async def _fake(query: str) -> list[WebSearchHit]:
        if fail_with is not None:
            raise fail_with("simulated web-search failure")
        for domain, platform in domain_to_platform.items():
            if f"site:{domain}" in query:
                return list(table.get(platform, []))
        return []

    return _fake


def _mock_all_direct_username(httpx_mock: HTTPXMock, handle: str) -> None:
    """Stub the four direct-probe adapters with hit fixtures."""
    httpx_mock.add_response(url=_pdfcoffee(handle), text=_PDFCOFFEE_HITS)
    httpx_mock.add_response(url=_yumpu(handle), text=_YUMPU_HITS)
    httpx_mock.add_response(url=_pastebin(handle), text=_PASTEBIN_USER_PAGE)
    httpx_mock.add_response(url=_archive(handle), json=_ARCHIVE_HITS_JSON)


def _mock_all_direct_email(httpx_mock: HTTPXMock, email: str) -> None:
    """Stub the email-supported direct adapters (pastebin is username-only)."""
    httpx_mock.add_response(url=_pdfcoffee(email), text=_PDFCOFFEE_HITS)
    httpx_mock.add_response(url=_yumpu(email), text=_YUMPU_HITS)
    httpx_mock.add_response(url=_archive(email), json=_ARCHIVE_HITS_JSON)


# ---------------------------------------------------------------------- tests


async def test_skips_unsupported_identifier() -> None:
    """Wallet / phone / etc. identifiers are skipped without any network call."""
    collector = DocLeakCollector()
    ident = Identifier(type=IdentifierType.PHONE, value="+14155550000")
    assert await collector.collect(ident) == []


async def test_supports_username_and_email() -> None:
    collector = DocLeakCollector()
    assert collector.supports(Identifier(type=IdentifierType.USERNAME, value="alice"))
    assert collector.supports(Identifier(type=IdentifierType.EMAIL, value="a@b.co"))
    assert not collector.supports(Identifier(type=IdentifierType.WALLET, value="0x" + "a" * 40))
    assert not collector.supports(Identifier(type=IdentifierType.URL, value="https://e.g"))


async def test_username_invalid_handle_short_circuits() -> None:
    """Handles with shell-special / control bytes don't reach the network."""
    collector = DocLeakCollector()
    bad = Identifier(type=IdentifierType.USERNAME, value="alice;rm -rf /")
    assert await collector.collect(bad) == []


async def test_username_collect_emits_one_trace_per_site(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """All 12 platforms (4 direct + 8 web-search) emit one trace each."""
    _mock_all_direct_username(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client, web_search_fn=_make_fake_web_search())
        traces = await collector.collect(user_alice)

    by = _by_platform(traces)
    expected = {
        "archive_org",
        "pdfcoffee",
        "yumpu",
        "pastebin",
        *_WEB_SEARCH_PLATFORMS,
    }
    assert set(by) == expected
    for trace in traces:
        assert trace.source == TraceSource.DOC_LEAK
        assert trace.fields["query"] == "alice"
        assert trace.fields["identifier_kind"] == "username"


async def test_email_collect_skips_pastebin(httpx_mock: HTTPXMock, email_alice: Identifier) -> None:
    """Pastebin has no useful per-site search by email, so the adapter
    is intentionally skipped — we should NOT see a pastebin trace and
    NOT have made a request to pastebin."""
    _mock_all_direct_email(httpx_mock, "alice@example.com")
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client, web_search_fn=_make_fake_web_search())
        traces = await collector.collect(email_alice)

    by = _by_platform(traces)
    assert "pastebin" not in by
    assert {
        "archive_org",
        "pdfcoffee",
        "yumpu",
        *_WEB_SEARCH_PLATFORMS,
    } == set(by)
    for trace in traces:
        assert trace.fields["query"] == "alice@example.com"
        assert trace.fields["identifier_kind"] == "email"

    pastebin_hits = [r for r in httpx_mock.get_requests() if "pastebin.com" in str(r.url)]
    assert pastebin_hits == []


async def test_email_canonicalised_lowercase(httpx_mock: HTTPXMock) -> None:
    ident = Identifier(type=IdentifierType.EMAIL, value="Alice@Example.COM")
    _mock_all_direct_email(httpx_mock, "alice@example.com")
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client, web_search_fn=_make_fake_web_search())
        traces = await collector.collect(ident)
    by = _by_platform(traces)
    assert by["pdfcoffee"]["query"] == "alice@example.com"
    assert by["scribd"]["query"] == "alice@example.com"


# ----------------------------------------------- direct-probe HTML adapter tests


async def test_pdfcoffee_existing_hits_are_parsed(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """pdfcoffee returns hits in initial HTML; the regex anchors them."""
    _mock_all_direct_username(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client, web_search_fn=_make_fake_web_search())
        traces = await collector.collect(user_alice)
    by = _by_platform(traces)
    pdfcoffee = by["pdfcoffee"]
    assert pdfcoffee["presence_status"] == "exists"
    assert pdfcoffee["hit_count"] == 2
    urls = {hit["url"] for hit in pdfcoffee["hits"]}
    assert urls == {
        "https://pdfcoffee.com/alice-leak-2023-pdf-free.html",
        "https://pdfcoffee.com/alice-bio-pdf-free.html",
    }


async def test_yumpu_existing_hits_are_parsed(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """yumpu serves hits inline; the regex captures both /view/ and /read/ URLs."""
    _mock_all_direct_username(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client, web_search_fn=_make_fake_web_search())
        traces = await collector.collect(user_alice)
    yumpu = _by_platform(traces)["yumpu"]
    assert yumpu["presence_status"] == "exists"
    assert yumpu["hit_count"] == 2


async def test_pdfcoffee_zero_hits_marks_not_found(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """Clean 200 with no platform-shape URLs in the body -> not_found."""
    httpx_mock.add_response(url=_pdfcoffee("alice"), text=_EMPTY_BODY)
    httpx_mock.add_response(url=_yumpu("alice"), text=_EMPTY_BODY)
    httpx_mock.add_response(url=_pastebin("alice"), text=_PASTEBIN_NO_USER)
    httpx_mock.add_response(url=_archive("alice"), json=_ARCHIVE_NO_HITS_JSON)

    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(
            client=client,
            web_search_fn=_make_fake_web_search(hits_by_platform={}),
        )
        traces = await collector.collect(user_alice)
    by = _by_platform(traces)
    for platform in ("pdfcoffee", "yumpu", "pastebin"):
        assert by[platform]["presence_status"] == "not_found", platform
        assert by[platform]["hit_count"] == 0
        assert by[platform]["hits"] == []


async def test_html_search_4xx_marks_blocked(httpx_mock: HTTPXMock, user_alice: Identifier) -> None:
    """A 4xx response is always 'blocked', not 'not_found'."""
    httpx_mock.add_response(url=_pdfcoffee("alice"), status_code=403, text="forbidden")
    httpx_mock.add_response(url=_yumpu("alice"), text=_YUMPU_HITS)
    httpx_mock.add_response(url=_pastebin("alice"), text=_PASTEBIN_USER_PAGE)
    httpx_mock.add_response(url=_archive("alice"), json=_ARCHIVE_HITS_JSON)

    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client, web_search_fn=_make_fake_web_search())
        traces = await collector.collect(user_alice)
    pdfcoffee = _by_platform(traces)["pdfcoffee"]
    assert pdfcoffee["presence_status"] == "blocked"
    assert pdfcoffee["http_status"] == 403
    assert pdfcoffee["hit_count"] == 0


async def test_html_search_anti_bot_interstitial_marks_blocked(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """Cloudflare 'checking your browser' 200s must be flagged blocked,
    NOT not_found, so we never falsely conclude absence from anti-bot."""
    interstitial = (
        "<html><head><title>Just a moment...</title></head><body>"
        "Checking your browser before accessing pdfcoffee.com" + ("x" * 300) + "</body></html>"
    )
    httpx_mock.add_response(url=_pdfcoffee("alice"), text=interstitial)
    httpx_mock.add_response(url=_yumpu("alice"), text=_YUMPU_HITS)
    httpx_mock.add_response(url=_pastebin("alice"), text=_PASTEBIN_USER_PAGE)
    httpx_mock.add_response(url=_archive("alice"), json=_ARCHIVE_HITS_JSON)

    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client, web_search_fn=_make_fake_web_search())
        traces = await collector.collect(user_alice)
    pdfcoffee = _by_platform(traces)["pdfcoffee"]
    assert pdfcoffee["presence_status"] == "blocked"
    assert "anti-bot" in pdfcoffee["evidence_marker"]


async def test_html_search_short_body_marks_unverified(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """Tiny SPA-shell bodies don't differentiate exists/not_found ->
    unverified, never not_found."""
    httpx_mock.add_response(url=_pdfcoffee("alice"), text="<html><body></body></html>")
    httpx_mock.add_response(url=_yumpu("alice"), text=_YUMPU_HITS)
    httpx_mock.add_response(url=_pastebin("alice"), text=_PASTEBIN_USER_PAGE)
    httpx_mock.add_response(url=_archive("alice"), json=_ARCHIVE_HITS_JSON)

    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client, web_search_fn=_make_fake_web_search())
        traces = await collector.collect(user_alice)
    pdfcoffee = _by_platform(traces)["pdfcoffee"]
    assert pdfcoffee["presence_status"] == "unverified"


# ----------------------------------------------- archive.org JSON adapter tests


async def test_archive_org_json_parsed_with_numfound(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """archive.org's numFound (not the docs sample size) is the
    authoritative hit count surfaced to the dossier."""
    _mock_all_direct_username(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client, web_search_fn=_make_fake_web_search())
        traces = await collector.collect(user_alice)
    archive = _by_platform(traces)["archive_org"]
    assert archive["presence_status"] == "exists"
    assert archive["hit_count"] == 4
    assert len(archive["hits"]) == 2
    assert archive["hits"][0]["url"].startswith("https://archive.org/details/")


async def test_archive_org_4xx_marks_blocked(httpx_mock: HTTPXMock, user_alice: Identifier) -> None:
    httpx_mock.add_response(url=_pdfcoffee("alice"), text=_PDFCOFFEE_HITS)
    httpx_mock.add_response(url=_yumpu("alice"), text=_YUMPU_HITS)
    httpx_mock.add_response(url=_pastebin("alice"), text=_PASTEBIN_USER_PAGE)
    httpx_mock.add_response(url=_archive("alice"), status_code=503)

    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client, web_search_fn=_make_fake_web_search())
        traces = await collector.collect(user_alice)
    archive = _by_platform(traces)["archive_org"]
    assert archive["presence_status"] == "blocked"
    assert archive["http_status"] == 503


async def test_archive_org_zero_results_marks_not_found(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    httpx_mock.add_response(url=_pdfcoffee("alice"), text=_EMPTY_BODY)
    httpx_mock.add_response(url=_yumpu("alice"), text=_EMPTY_BODY)
    httpx_mock.add_response(url=_pastebin("alice"), text=_PASTEBIN_NO_USER)
    httpx_mock.add_response(url=_archive("alice"), json=_ARCHIVE_NO_HITS_JSON)

    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(
            client=client,
            web_search_fn=_make_fake_web_search(hits_by_platform={}),
        )
        traces = await collector.collect(user_alice)
    archive = _by_platform(traces)["archive_org"]
    assert archive["presence_status"] == "not_found"
    assert archive["hit_count"] == 0


# ------------------------------------------------ web-search adapter behaviour


async def test_web_search_probes_emit_exists_with_filtered_citations(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """Each web-search platform should surface exactly the citations
    whose URL matches its per-platform regex (off-domain ad URLs are
    filtered out)."""
    _mock_all_direct_username(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client, web_search_fn=_make_fake_web_search())
        traces = await collector.collect(user_alice)

    by = _by_platform(traces)
    for platform in _WEB_SEARCH_PLATFORMS:
        fields = by[platform]
        assert fields["presence_status"] == "exists", platform
        assert fields["hit_count"] >= 1, platform
        # The off-domain example.com ad URL we seeded for scribd MUST be filtered.
        for hit in fields["hits"]:
            assert "example.com" not in hit["url"], platform
        # http_status is None for web-search-routed sites — we never
        # made a direct call to the platform.
        assert fields["http_status"] is None
        assert "web_search" in fields["evidence_marker"]


async def test_web_search_zero_citations_marks_not_found(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """An empty citation list from the LLM is 'not_found', not 'blocked'."""
    _mock_all_direct_username(httpx_mock, "alice")
    fake = _make_fake_web_search(hits_by_platform={})
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client, web_search_fn=fake)
        traces = await collector.collect(user_alice)
    by = _by_platform(traces)
    for platform in _WEB_SEARCH_PLATFORMS:
        assert by[platform]["presence_status"] == "not_found", platform
        assert by[platform]["hit_count"] == 0


async def test_web_search_backend_error_marks_blocked(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """A WebSearchError from the backend translates to ``blocked``
    on the trace — presence cannot be inferred."""
    _mock_all_direct_username(httpx_mock, "alice")
    fake = _make_fake_web_search(fail_with=WebSearchError)
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client, web_search_fn=fake)
        traces = await collector.collect(user_alice)
    by = _by_platform(traces)
    for platform in _WEB_SEARCH_PLATFORMS:
        assert by[platform]["presence_status"] == "blocked", platform
        assert "web_search backend error" in by[platform]["evidence_marker"]


async def test_web_search_unwired_emits_unverified(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """When no web-search backend is wired in, web-search platforms
    emit 'unverified' rather than failing — the dossier still records
    that the platform was considered."""
    _mock_all_direct_username(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        # No web_search_fn passed — collector should still construct.
        collector = DocLeakCollector(client=client)
        traces = await collector.collect(user_alice)
    by = _by_platform(traces)
    for platform in _WEB_SEARCH_PLATFORMS:
        assert by[platform]["presence_status"] == "unverified", platform
        assert "no web-search backend" in by[platform]["evidence_marker"]
    # The four direct-probe platforms continue to work normally.
    for platform in ("archive_org", "pdfcoffee", "yumpu", "pastebin"):
        assert by[platform]["presence_status"] == "exists", platform


async def test_web_search_dork_includes_site_operator() -> None:
    """The collector queries the backend with ``site:<domain> "<query>"``.

    We capture queries against a stub fn to assert the dork shape — the
    LLM is only useful as a search backend when we anchor it to the
    target domain.
    """
    captured: list[str] = []

    async def _capturing(query: str) -> list[WebSearchHit]:
        captured.append(query)
        return []

    collector = DocLeakCollector(web_search_fn=_capturing)
    # Direct adapters need a mocked client; pass nothing and let them
    # error / produce 'blocked' traces. We only care about the dorks.
    async with httpx.AsyncClient() as client:
        collector._client = client
        await collector._web_search_probe(query="alice", kind="username", platform="scribd")
        await collector._web_search_probe(
            query="bob@example.com", kind="email", platform="dokumen_tips"
        )

    assert any('site:scribd.com "alice"' in q for q in captured)
    assert any('site:dokumen.tips "bob@example.com"' in q for q in captured)


# ------------------------------------------ evidence + user-agent invariants


async def test_evidence_drops_raw_payload_keeps_hash(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """Doc-leak responses can carry incidental PII in surrounding HTML
    (excerpts, nearby titles), so we MUST NOT inline the raw body. The
    hash + URL stay so the chain is auditable."""
    _mock_all_direct_username(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client, web_search_fn=_make_fake_web_search())
        traces = await collector.collect(user_alice)
    for trace in traces:
        assert trace.evidence.raw_payload is None
        assert trace.evidence.source_url
        assert len(trace.evidence.payload_sha256) == 64


async def test_uses_browser_user_agent(httpx_mock: HTTPXMock, user_alice: Identifier) -> None:
    """Every direct adapter sends a desktop browser UA — the platforms
    aggressively block default httpx UAs, so this is part of the contract."""
    _mock_all_direct_username(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client, web_search_fn=_make_fake_web_search())
        await collector.collect(user_alice)
    for request in httpx_mock.get_requests():
        ua = request.headers.get("User-Agent", "")
        assert "Mozilla/5.0" in ua, request.url
