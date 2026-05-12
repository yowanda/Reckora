"""Tests for the LeakHuntCollector.

Unlike :class:`DocLeakCollector` which fans out across a fixed list of
twelve platforms, ``LeakHuntCollector`` renders a small bank of
leak-vector query templates against the seed and lets the injected
:data:`WebSearchFn` decide which sites are relevant. These tests use
an inline fake ``WebSearchFn`` (no HTTP at all) to assert the
template rendering, error handling, and trace shape.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from reckora.collectors.leak_hunt import LeakHuntCollector
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource
from reckora.reasoning.web_search import WebSearchError, WebSearchHit

WebSearchFnT = Callable[[str], Awaitable[list[WebSearchHit]]]


@pytest.fixture
def user_alice() -> Identifier:
    return Identifier(type=IdentifierType.USERNAME, value="alice")


@pytest.fixture
def email_alice() -> Identifier:
    return Identifier(type=IdentifierType.EMAIL, value="alice@example.com")


def _hits(*urls: str) -> list[WebSearchHit]:
    return [WebSearchHit(url=u, title=u, snippet="") for u in urls]


def _make_fn(
    responder: Callable[[str], list[WebSearchHit] | Exception],
) -> tuple[WebSearchFnT, list[str]]:
    """Build a fake ``WebSearchFn`` and a list capturing the queries
    it was called with — handy for asserting template rendering."""
    captured: list[str] = []

    async def fn(query: str) -> list[WebSearchHit]:
        captured.append(query)
        result = responder(query)
        if isinstance(result, Exception):
            raise result
        return result

    return fn, captured


@pytest.mark.asyncio
async def test_collect_emits_one_trace_per_username_query(
    user_alice: Identifier,
) -> None:
    """USERNAME seeds get all five default templates rendered."""

    fn, captured = _make_fn(
        lambda q: _hits(
            f"https://example.com/leak/{hash(q) & 0xFFFF}",
            f"https://pastebin.com/{hash(q) & 0xFFF:03x}",
        )
    )
    collector = LeakHuntCollector(web_search_fn=fn)

    traces = await collector.collect(user_alice)

    # Five default username-applicable templates → five traces.
    assert len(traces) == 5
    assert all(trace.source == TraceSource.LEAK_HUNT for trace in traces)

    # Each trace carries presence_status=exists (we returned 2 hits each).
    statuses = {trace.fields["presence_status"] for trace in traces}
    assert statuses == {"exists"}

    # The rendered queries quote the seed verbatim and use the templates
    # we shipped, including the username-only ``inurl:"alice" (site:…)``.
    assert all('"alice"' in q for q in captured)
    assert any("filetype:pdf" in q for q in captured)
    assert any("pastebin" in q for q in captured)
    assert any("site:scribd.com" in q for q in captured)


@pytest.mark.asyncio
async def test_collect_skips_username_only_template_for_email(
    email_alice: Identifier,
) -> None:
    """EMAIL seeds skip the ``inurl:…`` template (USERNAME-only)."""

    fn, captured = _make_fn(lambda _q: [])
    collector = LeakHuntCollector(web_search_fn=fn)

    traces = await collector.collect(email_alice)

    # Four email-applicable templates (the ``inurl:…`` one is gated).
    assert len(traces) == 4
    assert not any("inurl:" in q for q in captured)
    # Email is lower-cased into the rendered query.
    assert all('"alice@example.com"' in q for q in captured)
    # Zero hits → not_found, not blocked.
    assert {trace.fields["presence_status"] for trace in traces} == {"not_found"}


@pytest.mark.asyncio
async def test_collect_deduplicates_hits_within_query(
    user_alice: Identifier,
) -> None:
    """Repeated URLs inside one backend response collapse to one entry."""

    duplicate = "https://www.scribd.com/document/1234/Alice"

    def responder(_q: str) -> list[WebSearchHit]:
        return _hits(duplicate, duplicate, "https://issuu.com/alice/docs/x")

    fn, _ = _make_fn(responder)
    collector = LeakHuntCollector(web_search_fn=fn)

    traces = await collector.collect(user_alice)
    for trace in traces:
        urls = [hit["url"] for hit in trace.fields["hits"]]
        # The duplicate scribd URL must appear at most once per trace.
        assert urls.count(duplicate) <= 1


@pytest.mark.asyncio
async def test_collect_marks_failed_query_blocked_not_fatal(
    user_alice: Identifier,
) -> None:
    """One failing query yields a ``blocked`` trace; the rest still run.

    This guards against the regression where a single backend HTTP 500
    nuked the entire leak-hunt pass for the seed.
    """

    def responder(q: str) -> list[WebSearchHit] | Exception:
        if "filetype:pdf" in q:
            return WebSearchError("backend returned HTTP 503")
        return _hits("https://example.com/x")

    fn, _ = _make_fn(responder)
    collector = LeakHuntCollector(web_search_fn=fn)

    traces = await collector.collect(user_alice)

    blocked = [t for t in traces if t.fields["presence_status"] == "blocked"]
    exists = [t for t in traces if t.fields["presence_status"] == "exists"]

    assert len(blocked) == 1
    assert "backend returned HTTP 503" in blocked[0].fields["evidence_marker"]
    assert len(exists) == 4  # the other four queries still ran


@pytest.mark.asyncio
async def test_collect_emits_unverified_trace_when_no_backend(
    user_alice: Identifier,
) -> None:
    """No ``web_search_fn`` => one ``unverified`` trace, not silence.

    Matches ``DocLeakCollector``'s behaviour: the dossier records that
    the leak-hunt surface was *considered* but skipped, so analysts
    aren't left wondering whether it ran at all.
    """

    collector = LeakHuntCollector(web_search_fn=None)
    traces = await collector.collect(user_alice)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.source == TraceSource.LEAK_HUNT
    assert trace.fields["presence_status"] == "unverified"
    assert "no web_search backend" in trace.fields["evidence_marker"]


@pytest.mark.asyncio
async def test_collect_rejects_invalid_seed_value(user_alice: Identifier) -> None:
    """Seeds that fail the conservative regex never hit the backend."""

    fn, captured = _make_fn(lambda _q: [])
    collector = LeakHuntCollector(web_search_fn=fn)

    bad = Identifier(type=IdentifierType.USERNAME, value="not a username!@#")
    traces = await collector.collect(bad)

    assert traces == []
    assert captured == []  # no backend calls at all


@pytest.mark.asyncio
async def test_collect_hits_capped_at_max_hits_per_query(
    user_alice: Identifier,
) -> None:
    """Each trace's ``hits`` list is bounded by ``_MAX_HITS_PER_QUERY``.

    The total ``hit_count`` reflects the (capped) unique URL count, not
    the raw backend response length.
    """

    fn, _ = _make_fn(lambda _q: _hits(*[f"https://example.com/page/{i}" for i in range(30)]))
    collector = LeakHuntCollector(web_search_fn=fn)

    traces = await collector.collect(user_alice)
    for trace in traces:
        # ``hits`` capped at _MAX_HITS_PER_QUERY (8) regardless of input.
        assert len(trace.fields["hits"]) <= 8
        # hit_count is the (capped) unique-URL count, not the raw 30.
        assert trace.fields["hit_count"] == 12  # _WEB_SEARCH_LIMIT dedup cap


@pytest.mark.asyncio
async def test_collect_returns_empty_for_unsupported_identifier() -> None:
    """Non-username/email identifiers short-circuit without calling
    the backend (``supports()`` returns False)."""

    fn, captured = _make_fn(lambda _q: [])
    collector = LeakHuntCollector(web_search_fn=fn)

    phone = Identifier(type=IdentifierType.PHONE, value="+15551234567")
    traces = await collector.collect(phone)

    assert traces == []
    assert captured == []
