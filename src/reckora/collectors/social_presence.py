"""Social presence probe — best-effort lookups for auth-walled platforms.

Instagram, Threads, LinkedIn and Facebook all gate their public profile
pages behind login walls and aggressive anti-bot heuristics. Unlike
Reddit / Hacker News / TikTok / X, they do not expose a stable public
JSON endpoint we can query for canonical profile data; the most we can
do without an authenticated session is a URL probe that records:

- the canonical profile URL the user *would* live at on each platform
- a ``presence_status`` triage flag describing what (if anything) the
  platform told us:

  * ``exists`` — server returned content we could positively attribute
    to the requested handle (e.g. Instagram's ``web_profile_info`` JSON
    API came back with a user record; LinkedIn's title contained the
    handle / a real name)
  * ``not_found`` — the platform's own "user not found" sentinel fired
    (Instagram 404, LinkedIn 404, Facebook 404)
  * ``blocked`` — anti-bot or rate-limit mechanism rejected the probe
    (LinkedIn HTTP 999, Instagram 429, Facebook 400 login redirect);
    presence is not knowable from this run
  * ``unverified`` — the platform served a generic shell that doesn't
    differentiate existence (Threads SPA, Facebook static login wall);
    presence cannot be inferred without auth

We always emit one :class:`Trace` per platform so the dossier records
that the platform was *considered* — the ``presence_status`` flag tells
the analyst whether the URL was actually verified or just minted.

The schema for each emitted trace's ``fields``:

- ``platform`` — ``"instagram"`` / ``"threads"`` / ``"linkedin"`` /
  ``"facebook"``
- ``profile_url`` — canonical URL, ``https://www.instagram.com/<u>/``,
  ``https://www.threads.net/@<u>``, ``https://www.linkedin.com/in/<u>/``,
  ``https://www.facebook.com/<u>``
- ``handle`` — the seed identifier value (lower-cased lookup key)
- ``presence_status`` — one of the four triage strings above
- ``http_status`` — int status code observed (or ``None`` on transport
  failure)
- ``display_name`` — populated only when ``presence_status == "exists"``
  AND the platform leaked a human-readable name (Instagram + LinkedIn
  do; Threads + Facebook don't)
- ``evidence_marker`` — short string describing *why* we picked the
  status (e.g. ``"web_profile_info: user found"``,
  ``"linkedin: title contained 'doesn't exist'"``); kept short so the
  dossier table stays scannable
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, ClassVar

import httpx

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

INSTAGRAM_PROFILE_BASE = "https://www.instagram.com"
INSTAGRAM_API_BASE = "https://i.instagram.com"
INSTAGRAM_WEB_APP_ID = "936619743392459"
THREADS_PROFILE_BASE = "https://www.threads.net"
LINKEDIN_PROFILE_BASE = "https://www.linkedin.com"
FACEBOOK_PROFILE_BASE = "https://www.facebook.com"

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Conservative across-platform username regex: 1-50 chars,
# alphanumerics + underscore + period + hyphen. Covers Instagram
# (1-30, ``a-z0-9._``), Threads (same as Instagram), LinkedIn vanity
# slugs (``a-z0-9-``) and Facebook usernames (``a-z0-9.``).
_HANDLE_RE = re.compile(r"^[A-Za-z0-9._\-]{1,50}$")
_LINKEDIN_NOT_FOUND_TITLES = (
    "page not found",
    "couldn't find",
    "page isn't available",
    "this page isn't",
    "404",
)
_TITLE_RE = re.compile(r"<title[^>]*>([^<]*)</title>", re.IGNORECASE)


class SocialPresenceProbeCollector(Collector):
    """Best-effort URL probes against IG / Threads / LinkedIn / Facebook.

    Emits one Trace per platform. Use ``presence_status`` field to
    decide whether the URL was verified or just minted as a candidate.
    """

    name: ClassVar[str] = "social_presence_probe"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.USERNAME.value})

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        timeout: float = 15.0,
    ) -> None:
        super().__init__(client)
        self._timeout = timeout

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        handle = identifier.value.strip().lstrip("@")
        if not handle or not _HANDLE_RE.match(handle):
            return []
        client = await self._http()
        results = await asyncio.gather(
            self._probe_instagram(client, handle),
            self._probe_threads(client, handle),
            self._probe_linkedin(client, handle),
            self._probe_facebook(client, handle),
            return_exceptions=False,
        )
        traces: list[Trace] = []
        for fields, source_url, evidence_payload in results:
            if fields is None:
                continue
            traces.append(
                Trace(
                    identifier=identifier,
                    source=TraceSource.SOCIAL_PRESENCE_PROBE,
                    fields=fields,
                    evidence=make_evidence(source_url, evidence_payload, keep_raw=False),
                ),
            )
        return traces

    # ------------------------------------------------------------------ Instagram

    async def _probe_instagram(
        self, client: httpx.AsyncClient, handle: str
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        """Probe Instagram's public ``web_profile_info`` endpoint.

        That endpoint is what Instagram's own webclient uses to render a
        profile page; it requires the public ``X-IG-App-ID`` header but
        no auth and returns clean JSON with ``data.user.<...>`` for
        existing accounts and HTTP 404 for missing ones.
        """
        api_url = f"{INSTAGRAM_API_BASE}/api/v1/users/web_profile_info/?username={handle}"
        profile_url = f"{INSTAGRAM_PROFILE_BASE}/{handle}/"
        try:
            resp = await client.get(
                api_url,
                headers={
                    "Accept": "application/json",
                    "Accept-Language": "en-US,en;q=0.9",
                    "User-Agent": _BROWSER_UA,
                    "X-IG-App-ID": INSTAGRAM_WEB_APP_ID,
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            return (
                _make_fields(
                    platform="instagram",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="blocked",
                    http_status=None,
                    display_name=None,
                    evidence_marker=f"transport error: {type(exc).__name__}",
                ),
                api_url,
                {"error": type(exc).__name__},
            )
        status = resp.status_code
        evidence_payload: dict[str, Any] = {"http_status": status}
        if status == 404:
            return (
                _make_fields(
                    platform="instagram",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="not_found",
                    http_status=status,
                    display_name=None,
                    evidence_marker="web_profile_info: 404",
                ),
                api_url,
                evidence_payload,
            )
        if status == 401:
            return (
                _make_fields(
                    platform="instagram",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="blocked",
                    http_status=status,
                    display_name=None,
                    evidence_marker="web_profile_info: 401 (rate-limited / login required)",
                ),
                api_url,
                evidence_payload,
            )
        if status == 429:
            return (
                _make_fields(
                    platform="instagram",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="blocked",
                    http_status=status,
                    display_name=None,
                    evidence_marker="web_profile_info: 429 rate limit",
                ),
                api_url,
                evidence_payload,
            )
        if status >= 400:
            return (
                _make_fields(
                    platform="instagram",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="blocked",
                    http_status=status,
                    display_name=None,
                    evidence_marker=f"web_profile_info: HTTP {status}",
                ),
                api_url,
                evidence_payload,
            )
        # 2xx: parse JSON for the user record. Instagram returns
        # ``{"data": {"user": null}}`` for some takedown'd accounts;
        # treat that as ``not_found`` even on 200.
        try:
            data = resp.json()
        except ValueError:
            return (
                _make_fields(
                    platform="instagram",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="unverified",
                    http_status=status,
                    display_name=None,
                    evidence_marker="web_profile_info: 200 with non-JSON body",
                ),
                api_url,
                evidence_payload,
            )
        user = _safe_dict(_safe_dict(_safe_dict(data).get("data")).get("user"))
        if not user:
            return (
                _make_fields(
                    platform="instagram",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="not_found",
                    http_status=status,
                    display_name=None,
                    evidence_marker="web_profile_info: 200 with empty user",
                ),
                api_url,
                evidence_payload,
            )
        evidence_payload["user_pk"] = _str_or_none(user.get("pk"))
        return (
            _make_fields(
                platform="instagram",
                profile_url=profile_url,
                handle=_str_or_none(user.get("username")) or handle,
                presence_status="exists",
                http_status=status,
                display_name=_str_or_none(user.get("full_name")),
                evidence_marker="web_profile_info: user found",
                extra={
                    "is_private": bool(user.get("is_private")),
                    "is_verified": bool(user.get("is_verified")),
                    "biography": _str_or_none(user.get("biography")),
                    "follower_count": _int_or_none(user.get("edge_followed_by", {}).get("count"))
                    if isinstance(user.get("edge_followed_by"), dict)
                    else None,
                },
            ),
            api_url,
            evidence_payload,
        )

    # ------------------------------------------------------------------ Threads

    async def _probe_threads(
        self, client: httpx.AsyncClient, handle: str
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        """Probe Threads. The web app is a hard SPA so we cannot
        differentiate "exists" from "missing" without executing JS;
        we just confirm the URL is reachable and hand back the URL."""
        profile_url = f"{THREADS_PROFILE_BASE}/@{handle}"
        try:
            resp = await client.get(
                profile_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                    "User-Agent": _BROWSER_UA,
                    "Referer": "https://www.google.com/",
                },
                timeout=self._timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            return (
                _make_fields(
                    platform="threads",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="blocked",
                    http_status=None,
                    display_name=None,
                    evidence_marker=f"transport error: {type(exc).__name__}",
                ),
                profile_url,
                {"error": type(exc).__name__},
            )
        status = resp.status_code
        evidence_payload: dict[str, Any] = {"http_status": status}
        # Threads doesn't bother returning 404 for missing handles —
        # both existing and missing get a 200 + the SPA shell. The only
        # signal in the static HTML is the title (always "Threads") so
        # we surface the URL as ``unverified`` regardless.
        if status >= 400 and status != 404:
            return (
                _make_fields(
                    platform="threads",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="blocked",
                    http_status=status,
                    display_name=None,
                    evidence_marker=f"threads: HTTP {status}",
                ),
                profile_url,
                evidence_payload,
            )
        if status == 404:
            return (
                _make_fields(
                    platform="threads",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="not_found",
                    http_status=status,
                    display_name=None,
                    evidence_marker="threads: 404",
                ),
                profile_url,
                evidence_payload,
            )
        return (
            _make_fields(
                platform="threads",
                profile_url=profile_url,
                handle=handle,
                presence_status="unverified",
                http_status=status,
                display_name=None,
                evidence_marker="threads: 200 (SPA shell, presence not knowable)",
            ),
            profile_url,
            evidence_payload,
        )

    # ------------------------------------------------------------------ LinkedIn

    async def _probe_linkedin(
        self, client: httpx.AsyncClient, handle: str
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        """Probe LinkedIn's public ``/in/<slug>`` page.

        LinkedIn is hostile to bots: a fresh request usually serves the
        rendered profile (HTTP 200 with ``<title>Name | LinkedIn</title>``
        for live accounts and a "Page not found" title otherwise), but
        repeated requests get throttled with HTTP 999 (LinkedIn's
        custom anti-bot status). We honour all three: 200+title for
        ``exists`` / ``not_found``, 404 for ``not_found``, 999 for
        ``blocked``.
        """
        profile_url = f"{LINKEDIN_PROFILE_BASE}/in/{handle}/"
        try:
            resp = await client.get(
                profile_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                    "User-Agent": _BROWSER_UA,
                    "Referer": "https://www.google.com/",
                },
                timeout=self._timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            return (
                _make_fields(
                    platform="linkedin",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="blocked",
                    http_status=None,
                    display_name=None,
                    evidence_marker=f"transport error: {type(exc).__name__}",
                ),
                profile_url,
                {"error": type(exc).__name__},
            )
        status = resp.status_code
        evidence_payload: dict[str, Any] = {"http_status": status}
        if status == 999:
            return (
                _make_fields(
                    platform="linkedin",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="blocked",
                    http_status=status,
                    display_name=None,
                    evidence_marker="linkedin: HTTP 999 (anti-bot)",
                ),
                profile_url,
                evidence_payload,
            )
        if status == 404:
            return (
                _make_fields(
                    platform="linkedin",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="not_found",
                    http_status=status,
                    display_name=None,
                    evidence_marker="linkedin: 404",
                ),
                profile_url,
                evidence_payload,
            )
        if status >= 400:
            return (
                _make_fields(
                    platform="linkedin",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="blocked",
                    http_status=status,
                    display_name=None,
                    evidence_marker=f"linkedin: HTTP {status}",
                ),
                profile_url,
                evidence_payload,
            )
        title = _extract_title(resp.text)
        evidence_payload["title"] = title
        if title is None:
            return (
                _make_fields(
                    platform="linkedin",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="unverified",
                    http_status=status,
                    display_name=None,
                    evidence_marker="linkedin: 200 with no <title>",
                ),
                profile_url,
                evidence_payload,
            )
        title_lc = title.lower()
        if any(marker in title_lc for marker in _LINKEDIN_NOT_FOUND_TITLES):
            return (
                _make_fields(
                    platform="linkedin",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="not_found",
                    http_status=status,
                    display_name=None,
                    evidence_marker=f"linkedin: title='{title[:80]}'",
                ),
                profile_url,
                evidence_payload,
            )
        # LinkedIn renders profile titles as ``"<Name> - <Headline> | LinkedIn"``.
        display_name: str | None = None
        if "| linkedin" in title_lc:
            head = title.rsplit("|", 1)[0].strip()
            if " - " in head:
                head = head.split(" - ", 1)[0].strip()
            display_name = head or None
        return (
            _make_fields(
                platform="linkedin",
                profile_url=profile_url,
                handle=handle,
                presence_status="exists",
                http_status=status,
                display_name=display_name,
                evidence_marker=f"linkedin: title='{title[:80]}'",
            ),
            profile_url,
            evidence_payload,
        )

    # ------------------------------------------------------------------ Facebook

    async def _probe_facebook(
        self, client: httpx.AsyncClient, handle: str
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        """Probe Facebook. Always serves a login wall for unauthenticated
        clients, so we cannot positively verify presence; emit the URL
        as ``unverified`` unless the server returns a clean 404."""
        profile_url = f"{FACEBOOK_PROFILE_BASE}/{handle}"
        try:
            resp = await client.get(
                profile_url,
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
                    platform="facebook",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="blocked",
                    http_status=None,
                    display_name=None,
                    evidence_marker=f"transport error: {type(exc).__name__}",
                ),
                profile_url,
                {"error": type(exc).__name__},
            )
        status = resp.status_code
        evidence_payload: dict[str, Any] = {"http_status": status}
        final_url = str(resp.url)
        evidence_payload["final_url"] = final_url
        if status == 404:
            return (
                _make_fields(
                    platform="facebook",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="not_found",
                    http_status=status,
                    display_name=None,
                    evidence_marker="facebook: 404",
                ),
                profile_url,
                evidence_payload,
            )
        # Facebook always redirects unauthenticated probes to the login
        # form, so a 200/400 response on ``/login/`` is not evidence of
        # presence either way. Surface as ``unverified`` so the analyst
        # knows the URL was minted but not verified.
        if "/login" in final_url or "login" in final_url:
            return (
                _make_fields(
                    platform="facebook",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="unverified",
                    http_status=status,
                    display_name=None,
                    evidence_marker="facebook: redirected to login wall",
                ),
                profile_url,
                evidence_payload,
            )
        if status >= 400:
            return (
                _make_fields(
                    platform="facebook",
                    profile_url=profile_url,
                    handle=handle,
                    presence_status="blocked",
                    http_status=status,
                    display_name=None,
                    evidence_marker=f"facebook: HTTP {status}",
                ),
                profile_url,
                evidence_payload,
            )
        title = _extract_title(resp.text)
        evidence_payload["title"] = title
        return (
            _make_fields(
                platform="facebook",
                profile_url=profile_url,
                handle=handle,
                presence_status="unverified",
                http_status=status,
                display_name=None,
                evidence_marker=(
                    f"facebook: 200 title='{title[:80]}' (presence not knowable)"
                    if title
                    else "facebook: 200 (presence not knowable)"
                ),
            ),
            profile_url,
            evidence_payload,
        )


def _make_fields(
    *,
    platform: str,
    profile_url: str,
    handle: str,
    presence_status: str,
    http_status: int | None,
    display_name: str | None,
    evidence_marker: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "platform": platform,
        "profile_url": profile_url,
        "handle": handle,
        "presence_status": presence_status,
        "http_status": http_status,
        "display_name": display_name,
        "evidence_marker": evidence_marker,
    }
    if extra:
        fields.update(extra)
    return fields


def _extract_title(html: str) -> str | None:
    match = _TITLE_RE.search(html)
    if match is None:
        return None
    title = match.group(1).strip()
    return title or None


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
