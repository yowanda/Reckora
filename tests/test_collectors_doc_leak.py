"""Tests for the DocLeakCollector.

The collector probes 8 public document-share / paste sites in parallel
and emits one Trace per site. We mock the HTTP layer with
``pytest-httpx`` and feed each adapter a realistic-looking HTML / JSON
fixture so the parser is exercised end-to-end without going to the
network.
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


@pytest.fixture
def user_alice() -> Identifier:
    return Identifier(type=IdentifierType.USERNAME, value="alice")


@pytest.fixture
def email_alice() -> Identifier:
    return Identifier(type=IdentifierType.EMAIL, value="alice@example.com")


# --------------------------------------------------------------- URL helpers


def _scribd(q: str) -> str:
    return f"https://www.scribd.com/search?query={quote_plus(q)}"


def _pdfcoffee(q: str) -> str:
    return f"https://pdfcoffee.com/?s={quote_plus(q)}"


def _pdfslide(q: str) -> str:
    return f"https://pdfslide.net/search?q={quote_plus(q)}"


def _slideshare(q: str) -> str:
    return f"https://www.slideshare.net/search?q={quote_plus(q)}"


def _issuu(q: str) -> str:
    return f"https://issuu.com/search?q={quote_plus(q)}"


def _4shared(q: str) -> str:
    return f"https://search.4shared.com/q/CCAD/1/{quote_plus(q)}"


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


# ----------------------------------------------------------------- fixtures


_SCRIBD_HITS = """
<html><body>
<div class="search-results">
  <a href="https://www.scribd.com/document/123456/Alice-Resume">Alice's resume PDF</a>
  <a href="https://www.scribd.com/document/789012/AliceCorpDataLeak">AliceCorp data leak</a>
  <a href="https://www.scribd.com/explore/News">irrelevant nav link</a>
</div>
</body></html>
"""

_PDFCOFFEE_HITS = """
<html><body>
<article><a href="https://pdfcoffee.com/alice-leak-2023-pdf-free.html">Alice leak 2023</a></article>
<article><a href="https://pdfcoffee.com/alice-bio-pdf-free.html">Alice bio</a></article>
</body></html>
"""

_PDFSLIDE_HITS = """
<html><body>
<a href="https://pdfslide.net/documents/alice-internal-memo-2022.html">Alice internal memo</a>
</body></html>
"""

_SLIDESHARE_HITS = """
<html><body>
<a href="https://www.slideshare.net/alice/credentials-2021">Alice credentials deck</a>
<a href="https://www.slideshare.net/slideshow/foo/alice-deck-q4">alice deck Q4</a>
</body></html>
"""

_ISSUU_HITS = """
<html><body>
<a href="https://issuu.com/alice/docs/private-notes">Alice private notes</a>
</body></html>
"""

_FOURSHARED_HITS = """
<html><body>
<a href="https://www.4shared.com/document/abc123/alice-leak.html">Alice leak</a>
<a href="https://www.4shared.com/file/xyz789/alice-archive.html">Alice archive</a>
</body></html>
"""

_PASTEBIN_USER_PAGE = """
<html><body>
<table class="maintable">
  <tr><td><a href="https://pastebin.com/AbCdEfGh">paste1</a></td></tr>
  <tr><td><a href="https://pastebin.com/12345678">paste2</a></td></tr>
</table>
</body></html>
"""

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


# -------------------------------------------- helper to mock all 8 endpoints


def _mock_all_username(httpx_mock: HTTPXMock, handle: str) -> None:
    """Stub every adapter for a username query with deterministic fixtures."""
    httpx_mock.add_response(url=_scribd(handle), text=_SCRIBD_HITS)
    httpx_mock.add_response(url=_pdfcoffee(handle), text=_PDFCOFFEE_HITS)
    httpx_mock.add_response(url=_pdfslide(handle), text=_PDFSLIDE_HITS)
    httpx_mock.add_response(url=_slideshare(handle), text=_SLIDESHARE_HITS)
    httpx_mock.add_response(url=_issuu(handle), text=_ISSUU_HITS)
    httpx_mock.add_response(url=_4shared(handle), text=_FOURSHARED_HITS)
    httpx_mock.add_response(url=_pastebin(handle), text=_PASTEBIN_USER_PAGE)
    httpx_mock.add_response(url=_archive(handle), json=_ARCHIVE_HITS_JSON)


def _mock_all_email(httpx_mock: HTTPXMock, email: str) -> None:
    """Stub every email-supported adapter (pastebin is skipped for emails)."""
    httpx_mock.add_response(url=_scribd(email), text=_SCRIBD_HITS)
    httpx_mock.add_response(url=_pdfcoffee(email), text=_PDFCOFFEE_HITS)
    httpx_mock.add_response(url=_pdfslide(email), text=_PDFSLIDE_HITS)
    httpx_mock.add_response(url=_slideshare(email), text=_SLIDESHARE_HITS)
    httpx_mock.add_response(url=_issuu(email), text=_ISSUU_HITS)
    httpx_mock.add_response(url=_4shared(email), text=_FOURSHARED_HITS)
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
    _mock_all_username(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client)
        traces = await collector.collect(user_alice)

    by = _by_platform(traces)
    # All 8 platforms (7 HTML + archive.org JSON, plus pastebin) for username.
    expected = {
        "scribd",
        "pdfcoffee",
        "pdfslide",
        "slideshare",
        "issuu",
        "4shared",
        "pastebin",
        "archive_org",
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
    _mock_all_email(httpx_mock, "alice@example.com")
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client)
        traces = await collector.collect(email_alice)

    by = _by_platform(traces)
    assert "pastebin" not in by
    assert {
        "scribd",
        "pdfcoffee",
        "pdfslide",
        "slideshare",
        "issuu",
        "4shared",
        "archive_org",
    } == set(by)
    for trace in traces:
        assert trace.fields["query"] == "alice@example.com"
        assert trace.fields["identifier_kind"] == "email"

    pastebin_hits = [r for r in httpx_mock.get_requests() if "pastebin.com" in str(r.url)]
    assert pastebin_hits == []


async def test_email_canonicalised_lowercase(
    httpx_mock: HTTPXMock,
) -> None:
    ident = Identifier(type=IdentifierType.EMAIL, value="Alice@Example.COM")
    _mock_all_email(httpx_mock, "alice@example.com")
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client)
        traces = await collector.collect(ident)
    by = _by_platform(traces)
    assert by["scribd"]["query"] == "alice@example.com"


async def test_html_search_existing_hits_are_parsed(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    _mock_all_username(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client)
        traces = await collector.collect(user_alice)
    by = _by_platform(traces)
    scribd = by["scribd"]
    assert scribd["presence_status"] == "exists"
    # Two scribd hits in fixture (the /explore/News URL is filtered out
    # because it doesn't match the /document/<id>/ regex).
    assert scribd["hit_count"] == 2
    urls = {hit["url"] for hit in scribd["hits"]}
    assert urls == {
        "https://www.scribd.com/document/123456/Alice-Resume",
        "https://www.scribd.com/document/789012/AliceCorpDataLeak",
    }


async def test_html_search_zero_hits_marks_not_found(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """A clean 200 with no hits anchors -> not_found."""
    httpx_mock.add_response(url=_scribd("alice"), text=_EMPTY_BODY)
    httpx_mock.add_response(url=_pdfcoffee("alice"), text=_EMPTY_BODY)
    httpx_mock.add_response(url=_pdfslide("alice"), text=_EMPTY_BODY)
    httpx_mock.add_response(url=_slideshare("alice"), text=_EMPTY_BODY)
    httpx_mock.add_response(url=_issuu("alice"), text=_EMPTY_BODY)
    httpx_mock.add_response(url=_4shared("alice"), text=_EMPTY_BODY)
    httpx_mock.add_response(url=_pastebin("alice"), text=_PASTEBIN_NO_USER)
    httpx_mock.add_response(url=_archive("alice"), json=_ARCHIVE_NO_HITS_JSON)

    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client)
        traces = await collector.collect(user_alice)
    by = _by_platform(traces)
    for platform, fields in by.items():
        assert fields["presence_status"] == "not_found", platform
        assert fields["hit_count"] == 0
        assert fields["hits"] == []


async def test_html_search_4xx_marks_blocked(httpx_mock: HTTPXMock, user_alice: Identifier) -> None:
    """A 4xx response is always 'blocked', not 'not_found'."""
    httpx_mock.add_response(url=_scribd("alice"), status_code=403, text="forbidden")
    httpx_mock.add_response(url=_pdfcoffee("alice"), text=_PDFCOFFEE_HITS)
    httpx_mock.add_response(url=_pdfslide("alice"), text=_PDFSLIDE_HITS)
    httpx_mock.add_response(url=_slideshare("alice"), text=_SLIDESHARE_HITS)
    httpx_mock.add_response(url=_issuu("alice"), text=_ISSUU_HITS)
    httpx_mock.add_response(url=_4shared("alice"), text=_FOURSHARED_HITS)
    httpx_mock.add_response(url=_pastebin("alice"), text=_PASTEBIN_USER_PAGE)
    httpx_mock.add_response(url=_archive("alice"), json=_ARCHIVE_HITS_JSON)

    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client)
        traces = await collector.collect(user_alice)
    by = _by_platform(traces)
    assert by["scribd"]["presence_status"] == "blocked"
    assert by["scribd"]["http_status"] == 403
    assert by["scribd"]["hit_count"] == 0


async def test_html_search_anti_bot_interstitial_marks_blocked(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """Cloudflare 'checking your browser' 200s must be flagged blocked,
    NOT not_found, so we never falsely conclude absence from anti-bot."""
    interstitial = (
        "<html><head><title>Just a moment...</title></head><body>"
        "Checking your browser before accessing pdfcoffee.com" + ("x" * 300) + "</body></html>"
    )
    httpx_mock.add_response(url=_scribd("alice"), text=_SCRIBD_HITS)
    httpx_mock.add_response(url=_pdfcoffee("alice"), text=interstitial)
    httpx_mock.add_response(url=_pdfslide("alice"), text=_PDFSLIDE_HITS)
    httpx_mock.add_response(url=_slideshare("alice"), text=_SLIDESHARE_HITS)
    httpx_mock.add_response(url=_issuu("alice"), text=_ISSUU_HITS)
    httpx_mock.add_response(url=_4shared("alice"), text=_FOURSHARED_HITS)
    httpx_mock.add_response(url=_pastebin("alice"), text=_PASTEBIN_USER_PAGE)
    httpx_mock.add_response(url=_archive("alice"), json=_ARCHIVE_HITS_JSON)

    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client)
        traces = await collector.collect(user_alice)
    by = _by_platform(traces)
    assert by["pdfcoffee"]["presence_status"] == "blocked"
    assert "anti-bot" in by["pdfcoffee"]["evidence_marker"]


async def test_html_search_short_body_marks_unverified(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """Tiny SPA-shell bodies don't differentiate exists/not_found ->
    unverified, never not_found."""
    httpx_mock.add_response(url=_scribd("alice"), text="<html><body></body></html>")
    httpx_mock.add_response(url=_pdfcoffee("alice"), text=_PDFCOFFEE_HITS)
    httpx_mock.add_response(url=_pdfslide("alice"), text=_PDFSLIDE_HITS)
    httpx_mock.add_response(url=_slideshare("alice"), text=_SLIDESHARE_HITS)
    httpx_mock.add_response(url=_issuu("alice"), text=_ISSUU_HITS)
    httpx_mock.add_response(url=_4shared("alice"), text=_FOURSHARED_HITS)
    httpx_mock.add_response(url=_pastebin("alice"), text=_PASTEBIN_USER_PAGE)
    httpx_mock.add_response(url=_archive("alice"), json=_ARCHIVE_HITS_JSON)

    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client)
        traces = await collector.collect(user_alice)
    by = _by_platform(traces)
    assert by["scribd"]["presence_status"] == "unverified"


async def test_archive_org_json_parsed_with_numfound(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """archive.org's numFound (not the docs sample size) is the
    authoritative hit count surfaced to the dossier."""
    _mock_all_username(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client)
        traces = await collector.collect(user_alice)
    archive = _by_platform(traces)["archive_org"]
    assert archive["presence_status"] == "exists"
    # Fixture: numFound=4, docs=2 (sample). hit_count must reflect total.
    assert archive["hit_count"] == 4
    # Sample is capped to MAX_HITS=5 but here only 2 docs are returned.
    assert len(archive["hits"]) == 2
    assert archive["hits"][0]["url"].startswith("https://archive.org/details/")


async def test_archive_org_4xx_marks_blocked(httpx_mock: HTTPXMock, user_alice: Identifier) -> None:
    httpx_mock.add_response(url=_scribd("alice"), text=_SCRIBD_HITS)
    httpx_mock.add_response(url=_pdfcoffee("alice"), text=_PDFCOFFEE_HITS)
    httpx_mock.add_response(url=_pdfslide("alice"), text=_PDFSLIDE_HITS)
    httpx_mock.add_response(url=_slideshare("alice"), text=_SLIDESHARE_HITS)
    httpx_mock.add_response(url=_issuu("alice"), text=_ISSUU_HITS)
    httpx_mock.add_response(url=_4shared("alice"), text=_FOURSHARED_HITS)
    httpx_mock.add_response(url=_pastebin("alice"), text=_PASTEBIN_USER_PAGE)
    httpx_mock.add_response(url=_archive("alice"), status_code=503)

    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client)
        traces = await collector.collect(user_alice)
    archive = _by_platform(traces)["archive_org"]
    assert archive["presence_status"] == "blocked"
    assert archive["http_status"] == 503


async def test_archive_org_zero_results_marks_not_found(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    httpx_mock.add_response(url=_scribd("alice"), text=_EMPTY_BODY)
    httpx_mock.add_response(url=_pdfcoffee("alice"), text=_EMPTY_BODY)
    httpx_mock.add_response(url=_pdfslide("alice"), text=_EMPTY_BODY)
    httpx_mock.add_response(url=_slideshare("alice"), text=_EMPTY_BODY)
    httpx_mock.add_response(url=_issuu("alice"), text=_EMPTY_BODY)
    httpx_mock.add_response(url=_4shared("alice"), text=_EMPTY_BODY)
    httpx_mock.add_response(url=_pastebin("alice"), text=_PASTEBIN_NO_USER)
    httpx_mock.add_response(url=_archive("alice"), json=_ARCHIVE_NO_HITS_JSON)

    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client)
        traces = await collector.collect(user_alice)
    archive = _by_platform(traces)["archive_org"]
    assert archive["presence_status"] == "not_found"
    assert archive["hit_count"] == 0


async def test_evidence_drops_raw_payload_keeps_hash(
    httpx_mock: HTTPXMock, user_alice: Identifier
) -> None:
    """Doc-leak responses can carry incidental PII in surrounding HTML
    (excerpts, nearby titles), so we MUST NOT inline the raw body. The
    hash + URL stay so the chain is auditable."""
    _mock_all_username(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client)
        traces = await collector.collect(user_alice)
    for trace in traces:
        assert trace.evidence.raw_payload is None
        assert trace.evidence.source_url
        assert len(trace.evidence.payload_sha256) == 64


async def test_uses_browser_user_agent(httpx_mock: HTTPXMock, user_alice: Identifier) -> None:
    """Every adapter sends a desktop browser UA — the platforms aggressively
    block default httpx UAs, so this is part of the contract."""
    _mock_all_username(httpx_mock, "alice")
    async with httpx.AsyncClient() as client:
        collector = DocLeakCollector(client=client)
        await collector.collect(user_alice)
    for request in httpx_mock.get_requests():
        ua = request.headers.get("User-Agent", "")
        assert "Mozilla/5.0" in ua, request.url
