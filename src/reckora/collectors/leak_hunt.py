"""AI-driven open-ended leak / breach search.

Where :mod:`reckora.collectors.doc_leak` probes a fixed list of twelve
document-share / paste sites with per-platform regex anchors,
``LeakHuntCollector`` lets the search backend (OpenAI Responses
``web_search``, against the Bing index) decide which sites are
relevant for a given seed. We:

1. Render a small bank of leak-vector query templates against the seed
   (e.g. ``"<seed>" filetype:pdf credentials``, ``"<seed>" pastebin``).
2. Fan the queries out concurrently via :data:`WebSearchFn`.
3. Emit one :class:`Trace` per query with the unique hit URLs returned
   by the model, no per-site regex filtering.

The collector is *complementary* to ``DocLeakCollector``: doc-leak
gives a deterministic, platform-by-platform breakdown ("did the AI
find anything on Scribd specifically?"), leak-hunt gives an
open-ended view ("show me everything the AI thinks is a leak surface
for this seed"). The HTTP API wires both behind a single user-facing
toggle so the dossier carries both signals.

Like :class:`SocialPresenceProbeCollector` and ``DocLeakCollector``,
we always emit one trace per query — even on ``not_found`` /
``blocked`` — so dossiers record that the query was *considered*.

Trace shape (``Trace.fields``):

- ``query`` — the rendered query string sent to the backend
- ``query_template`` — the unrendered template (for analyst grouping)
- ``identifier_kind`` — ``"username"`` or ``"email"``
- ``presence_status`` — ``"exists"`` / ``"not_found"`` / ``"blocked"``
  / ``"unverified"`` (matches ``DocLeakCollector`` taxonomy)
- ``hit_count`` — number of unique URLs the backend returned
- ``hits`` — sample of up to ``_MAX_HITS_PER_QUERY`` hits, each
  ``{"url": ..., "title": ...}``
- ``evidence_marker`` — short triage string explaining the status
  (``"leak_hunt: 7 URLs via web_search"``, ``"backend error: ..."``)
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, ClassVar

import httpx

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from ..reasoning.web_search import WebSearchError, WebSearchFn, WebSearchHit
from .base import Collector

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,24}$")

# Maximum unique hit URLs persisted per query in ``fields["hits"]``.
# Picked to keep dossier JSON bounded; analysts who want more can
# re-run the query verbatim via ``query`` field.
_MAX_HITS_PER_QUERY = 8

# Per-query request limit forwarded to :data:`WebSearchFn`. Slightly
# higher than ``_MAX_HITS_PER_QUERY`` so the dedup pass has room to
# work without starving the trimmed list.
_WEB_SEARCH_LIMIT = 12

# Query templates. Each is rendered against the seed value using
# ``str.format(seed=value)``. Templates are tagged with the identifier
# kinds they apply to (``"username"`` and / or ``"email"``).
#
# Goals when picking templates:
#
# 1. Cover the main leak vectors analysts manually check (pastes,
#    public docs, credentials dumps, breach indexes).
# 2. Stay short — every query costs a backend call. Five is the
#    current sweet spot at ~25-30s wall clock per investigation.
# 3. Use double quotes around the seed so the backend treats it as a
#    phrase and doesn't tokenise across the literal value.
_QueryTemplate = tuple[str, frozenset[str]]
_QUERY_TEMPLATES: tuple[_QueryTemplate, ...] = (
    (
        '"{seed}" leak OR breach OR exposed',
        frozenset({IdentifierType.USERNAME.value, IdentifierType.EMAIL.value}),
    ),
    (
        '"{seed}" pastebin OR ghostbin OR rentry OR gist',
        frozenset({IdentifierType.USERNAME.value, IdentifierType.EMAIL.value}),
    ),
    (
        '"{seed}" filetype:pdf',
        frozenset({IdentifierType.USERNAME.value, IdentifierType.EMAIL.value}),
    ),
    (
        '"{seed}" "password" OR "credentials" OR "confidential"',
        frozenset({IdentifierType.USERNAME.value, IdentifierType.EMAIL.value}),
    ),
    (
        'inurl:"{seed}" (site:scribd.com OR site:slideshare.net OR '
        "site:issuu.com OR site:4shared.com OR site:docplayer.net)",
        frozenset({IdentifierType.USERNAME.value}),
    ),
)


class LeakHuntCollector(Collector):
    """AI-driven open-ended leak surface scan for a single seed.

    Parameters
    ----------
    client:
        Unused; kept for API parity with other collectors. Web-search
        traffic is sent through the injected :data:`WebSearchFn`,
        which manages its own HTTP client.
    web_search_fn:
        Required backend. When ``None`` the collector emits a single
        ``unverified`` trace explaining the missing backend (no
        platform-by-platform spam) so the dossier still records that
        leak-hunt was *attempted*.
    templates:
        Optional override of the query templates. Each entry is a
        ``(template_string, supported_kinds)`` pair; the template
        string MUST contain ``{seed}`` exactly once. The default
        bank is :data:`_QUERY_TEMPLATES`.
    """

    name: ClassVar[str] = "leak_hunt"
    supported: ClassVar[frozenset[str]] = frozenset(
        {IdentifierType.USERNAME.value, IdentifierType.EMAIL.value}
    )

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        web_search_fn: WebSearchFn | None = None,
        templates: tuple[_QueryTemplate, ...] | None = None,
    ) -> None:
        super().__init__(client)
        self._web_search_fn = web_search_fn
        self._templates = templates if templates is not None else _QUERY_TEMPLATES

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        kind = identifier.type.value
        seed = identifier.value.strip()
        if kind == IdentifierType.USERNAME.value:
            seed = seed.lstrip("@")
            if not _USERNAME_RE.match(seed):
                return []
        elif kind == IdentifierType.EMAIL.value:
            seed = seed.lower()
            if not _EMAIL_RE.match(seed):
                return []
        else:  # pragma: no cover — supports() guards this
            return []

        applicable = [(template, kinds) for template, kinds in self._templates if kind in kinds]
        if not applicable:
            return []

        if self._web_search_fn is None:
            return [
                self._unverified_trace(
                    identifier=identifier,
                    kind=kind,
                    marker=(
                        "leak-hunt skipped: no web_search backend configured "
                        "(set OPENAI_API_KEY or run `reckora auth login`)"
                    ),
                )
            ]

        rendered = [(template, template.format(seed=seed)) for template, _ in applicable]

        results = await asyncio.gather(
            *(self._run_query(query) for _, query in rendered),
            return_exceptions=False,
        )

        traces: list[Trace] = []
        for (template, query), (hits, error) in zip(rendered, results, strict=True):
            traces.append(
                self._trace_from_result(
                    identifier=identifier,
                    kind=kind,
                    template=template,
                    query=query,
                    hits=hits,
                    error=error,
                )
            )
        return traces

    async def _run_query(
        self,
        query: str,
    ) -> tuple[list[WebSearchHit], str | None]:
        """Call the backend; capture transport errors as marker text.

        Returning a ``(hits, error)`` pair instead of raising keeps the
        outer ``asyncio.gather`` simple and lets us emit a ``blocked``
        trace per failed query rather than nuking the whole pass.
        """
        assert self._web_search_fn is not None
        try:
            hits = await self._web_search_fn(query)
        except WebSearchError as exc:
            return [], str(exc)
        except Exception as exc:  # pragma: no cover — defensive
            return [], f"unexpected {type(exc).__name__}: {exc}"
        return hits, None

    def _trace_from_result(
        self,
        *,
        identifier: Identifier,
        kind: str,
        template: str,
        query: str,
        hits: list[WebSearchHit],
        error: str | None,
    ) -> Trace:
        if error is not None:
            fields = _make_fields(
                query=query,
                template=template,
                kind=kind,
                presence_status="blocked",
                hit_count=0,
                hits=[],
                evidence_marker=f"backend error: {error}",
            )
            return Trace(
                identifier=identifier,
                source=TraceSource.LEAK_HUNT,
                fields=fields,
                evidence=make_evidence(
                    "reckora://leak_hunt",
                    {"query": query, "error": error},
                    keep_raw=False,
                ),
            )

        unique: list[dict[str, str]] = []
        seen: set[str] = set()
        for hit in hits:
            url = hit.url.strip()
            if not url or url in seen:
                continue
            seen.add(url)
            unique.append({"url": url, "title": hit.title or url})
            if len(unique) >= _WEB_SEARCH_LIMIT:
                break

        hit_count = len(unique)
        presence_status = "exists" if hit_count > 0 else "not_found"
        marker = f"leak_hunt: {hit_count} URL{'s' if hit_count != 1 else ''} via web_search"
        fields = _make_fields(
            query=query,
            template=template,
            kind=kind,
            presence_status=presence_status,
            hit_count=hit_count,
            hits=unique[:_MAX_HITS_PER_QUERY],
            evidence_marker=marker,
        )
        return Trace(
            identifier=identifier,
            source=TraceSource.LEAK_HUNT,
            fields=fields,
            evidence=make_evidence(
                "reckora://leak_hunt",
                {"query": query, "hit_count": hit_count},
                keep_raw=False,
            ),
        )

    def _unverified_trace(
        self,
        *,
        identifier: Identifier,
        kind: str,
        marker: str,
    ) -> Trace:
        return Trace(
            identifier=identifier,
            source=TraceSource.LEAK_HUNT,
            fields=_make_fields(
                query="",
                template="",
                kind=kind,
                presence_status="unverified",
                hit_count=0,
                hits=[],
                evidence_marker=marker,
            ),
            evidence=make_evidence(
                "reckora://leak_hunt",
                {"reason": "no_backend"},
                keep_raw=False,
            ),
        )


def _make_fields(
    *,
    query: str,
    template: str,
    kind: str,
    presence_status: str,
    hit_count: int,
    hits: list[dict[str, str]],
    evidence_marker: str,
) -> dict[str, Any]:
    return {
        "query": query,
        "query_template": template,
        "identifier_kind": kind,
        "presence_status": presence_status,
        "hit_count": hit_count,
        "hits": hits,
        "evidence_marker": evidence_marker,
    }
