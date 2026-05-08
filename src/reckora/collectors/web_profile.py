"""Generic web profile collector — extracts OpenGraph + <title> from any URL.

This is the catch-all collector for `url`-typed identifiers. It does not parse
arbitrary HTML; it just lifts the OpenGraph tags and the `<title>` so that
later correlation rules can compare bios, avatars and display names across
platforms without each platform needing a hand-rolled adapter.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar
from urllib.parse import urlparse

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

OG_TAG_RE = re.compile(
    r"""<meta[^>]+property=["']og:([^"']+)["'][^>]+content=["']([^"']*)["']""",
    re.IGNORECASE,
)
TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE | re.DOTALL)


class WebProfileCollector(Collector):
    """Lift OpenGraph + title metadata from a profile URL."""

    name: ClassVar[str] = "web_profile"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.URL.value})

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        client = await self._http()
        url = identifier.value
        resp = await client.get(url, headers={"User-Agent": "Reckora/0.1"})
        if resp.status_code >= 400:
            return []
        body = resp.text
        og = self._extract_og(body)
        title = self._extract_title(body)
        fields: dict[str, Any] = {
            "platform": og.get("site_name") or self._host(url),
            "profile_url": url,
            "display_name": og.get("title") or title,
            "bio": og.get("description"),
            "avatar_url": og.get("image"),
            "og_type": og.get("type"),
        }
        evidence = make_evidence(
            url,
            {"status": resp.status_code, "og": og, "title": title},
        )
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.WEB_PROFILE,
                fields=fields,
                evidence=evidence,
            ),
        ]

    @staticmethod
    def _extract_og(body: str) -> dict[str, str]:
        return {m.group(1).lower(): m.group(2) for m in OG_TAG_RE.finditer(body)}

    @staticmethod
    def _extract_title(body: str) -> str | None:
        match = TITLE_RE.search(body)
        return match.group(1).strip() if match else None

    @staticmethod
    def _host(url: str) -> str:
        return urlparse(url).hostname or url
