"""Hacker News collector.

Resolves a ``username`` Identifier against the public Hacker News Firebase
API at ``https://hacker-news.firebaseio.com/v0/user/{id}.json`` — no key,
no registration, just the same anonymous read-only endpoint the official
HN apps use. The collector is wired into the default orchestrator so any
seed of ``--kind username`` whose value matches an HN account triggers it.

The HN user payload carries an ``about`` HTML blob (the public bio) and a
``submitted`` array that can run into the tens of thousands. We keep the
canonical SHA-256 of the raw envelope as the audit anchor and drop the
inline body (``keep_raw=False``) so the saved dossier doesn't grow with
every long-tenure user. Display fields are normalised to the same flat
schema the other username collectors emit so the correlation engine can
read ``bio`` / ``display_name`` / ``profile_url`` without caring which
platform produced the trace.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from typing import Any, ClassVar

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

HACKERNEWS_API_BASE = "https://hacker-news.firebaseio.com/v0"
HACKERNEWS_PROFILE_BASE = "https://news.ycombinator.com/user"

# HN usernames are 2-15 characters: letters, digits, underscores, hyphens.
# The endpoint will happily 200-with-null for anything else; pre-filtering
# saves a network round trip on obvious misses (and keeps the orchestrator
# from spending a request on Bitcoin addresses, URLs, etc.).
HACKERNEWS_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{2,15}$")

# HN bios are stored as HTML — we strip tags and decode entities so the
# bio_similarity correlation rule sees readable prose, but we keep the
# raw HTML in ``bio_html`` for callers who want to render it.
_TAG_RE = re.compile(r"<[^>]+>")


class HackerNewsCollector(Collector):
    """Collect a public Hacker News user profile."""

    name: ClassVar[str] = "hackernews"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.USERNAME.value})

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": "Reckora/0.1",
        }

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        username = identifier.value
        if not HACKERNEWS_USERNAME_RE.match(username):
            return []
        client = await self._http()
        url = f"{HACKERNEWS_API_BASE}/user/{username}.json"
        resp = await client.get(url, headers=self._headers())
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        # The HN endpoint returns the literal JSON value ``null`` (HTTP 200)
        # for accounts that do not exist. Treat that exactly the same as a
        # 404 so the orchestrator's miss-vs-error semantics stay consistent
        # across collectors.
        data = resp.json()
        if not isinstance(data, dict):
            return []
        fields = self._normalise(data, username=username)
        evidence = make_evidence(url, data, keep_raw=False)
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.HACKERNEWS_API,
                fields=fields,
                evidence=evidence,
            ),
        ]

    @staticmethod
    def _normalise(data: dict[str, Any], *, username: str) -> dict[str, Any]:
        about_html = data.get("about")
        bio = _strip_html(about_html) if isinstance(about_html, str) else None
        submitted = data.get("submitted")
        submission_count = len(submitted) if isinstance(submitted, list) else None
        karma = data.get("karma")
        # ``created`` is a Unix epoch (seconds, UTC). HN's API never
        # returns negative or fractional values in the wild; coerce
        # defensively because the rest of the pipeline assumes ISO 8601.
        created_raw = data.get("created")
        created_iso = (
            datetime.fromtimestamp(int(created_raw), tz=UTC).isoformat()
            if isinstance(created_raw, int | float)
            else None
        )
        # Match the GitHub collector's ``id`` semantics: prefer the
        # server-reported username (canonical casing) and fall back to
        # the requested value so the trace always carries a username.
        canonical_id = data.get("id") if isinstance(data.get("id"), str) else username
        # An HN account is "active" if it has any visible signal — karma
        # above the 1-point default OR at least one submission. Empty
        # accounts (registered but never posted) still surface as a
        # trace with ``is_active=False`` so the absence of activity is
        # itself an intelligence finding rather than a collection miss.
        is_active = bool((isinstance(karma, int) and karma > 1) or (submission_count or 0) > 0)
        return {
            "platform": "hackernews",
            "username": canonical_id,
            "profile_url": f"{HACKERNEWS_PROFILE_BASE}?id={canonical_id}",
            "bio": bio,
            "bio_html": about_html if isinstance(about_html, str) else None,
            "karma": karma if isinstance(karma, int) else None,
            "submission_count": submission_count,
            "created_at": created_iso,
            "is_active": is_active,
        }


def _strip_html(text: str) -> str:
    """Strip HTML tags and decode entities so HN bios become readable prose."""
    return html.unescape(_TAG_RE.sub("", text)).strip()
