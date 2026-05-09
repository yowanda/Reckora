"""X / Twitter collector — public ``syndication.twitter.com`` profile widget.

X (formerly Twitter) closed its public REST API behind paid tiers in
2023, but the JavaScript embed widgets that ship on millions of websites
still need to render profile cards anonymously. They do so by hitting
``https://syndication.twitter.com/srv/timeline-profile/screen-name/<u>``,
which serves a self-contained Next.js page whose ``__NEXT_DATA__`` blob
embeds the user's ``timeline.entries[*].content.tweet.user`` records.

That same endpoint is reachable from any HTTP client without an API key
or a logged-in session, returning a stable JSON shape the public Twitter
v1.1 API used to expose. We extract the ``user`` object from the first
timeline entry — that's the canonical profile X returns alongside the
embedded timeline.

Existence signal:

- existing user → ``pageProps.headerProps.screenName == "<u>"`` AND a
  populated ``timeline.entries`` list whose entries embed the user
  record (``entry.content.tweet.user``)
- unknown user → no ``headerProps`` and an empty ``timeline.entries``
  (the widget still renders, just blank)

A user with zero tweets has ``headerProps.screenName`` but an empty
``entries`` list. We surface them with a minimal trace so the dossier
records the existence signal even without per-tweet metadata.

The normalised :pyattr:`reckora.models.entity.Trace.fields` schema:

- ``platform`` — always ``"x"`` (correlation engine joins on this string)
- ``profile_url`` — ``https://x.com/<screen_name>``
- ``screen_name`` — canonical handle X echoed back (case-corrected)
- ``user_id`` — X's numeric user id (``id_str``)
- ``display_name`` — ``user.name``
- ``bio`` — ``user.description``
- ``avatar_url`` — ``profile_image_url_https`` (querystring stripped)
- ``banner_url`` — ``profile_banner_url`` or ``None``
- ``followers_count`` / ``friends_count`` / ``statuses_count`` — ints
- ``created_at`` — ISO 8601 string parsed from X's RFC 1123-ish format
- ``verified`` — boolean
- ``location`` — string or ``None``
- ``url`` — the t.co-shortened link X stores in ``entities.url`` or the
  bare ``user.url`` if no expansion is present

The raw embedded HTML is **not** kept inline (it's ~300 KB of timeline
markup); only the parsed ``user`` payload is hashed for the evidence
chain.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

X_SYNDICATION_BASE = "https://syndication.twitter.com"
X_PROFILE_HOST = "https://x.com"

# X handles are 1-15 ASCII alphanumerics + underscore. Reject obvious
# misses early instead of paying a 300 KB request to learn the same.
_X_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>',
    re.DOTALL,
)


class XCollector(Collector):
    """Collect a public X profile via ``syndication.twitter.com``.

    Parameters
    ----------
    client:
        Optional httpx client; tests inject one wired to ``pytest-httpx``.
    base_url:
        Override for tests; defaults to the public X syndication host.
    """

    name: ClassVar[str] = "x"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.USERNAME.value})

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        base_url: str = X_SYNDICATION_BASE,
    ) -> None:
        super().__init__(client)
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        # The syndication endpoint is happy with any browser-ish UA but
        # 403s on the bare ``python-httpx`` default.
        return {
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        handle = identifier.value.strip().lstrip("@")
        if not handle or not _X_HANDLE_RE.match(handle):
            return []
        url = f"{self._base_url}/srv/timeline-profile/screen-name/{handle}?showReplies=false"
        client = await self._http()
        resp = await client.get(url, headers=self._headers())
        # The widget always returns 200; non-existence is signalled in
        # the body. Genuine transport failures (>=400) still surface.
        if resp.status_code in (404, 410):
            return []
        resp.raise_for_status()
        next_data = _extract_next_data(resp.text)
        if next_data is None:
            return []
        page_props = _safe_dict(_safe_dict(next_data.get("props")).get("pageProps"))
        header_props = _safe_dict(page_props.get("headerProps"))
        canonical = _str_or_none(header_props.get("screenName"))
        if canonical is None:
            # No ``headerProps.screenName`` ⇒ X's widget refused to render
            # a header for this slug ⇒ unknown account.
            return []
        user = _extract_user(page_props)
        fields = _normalise(canonical=canonical, user=user)
        # Hash only the slice we depend on so the audit trail stays
        # stable across X's frequent timeline reorderings.
        evidence_payload: dict[str, Any] = {"screen_name": canonical}
        if user is not None:
            evidence_payload["user"] = user
        evidence = make_evidence(url, evidence_payload, keep_raw=False)
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.X_SYNDICATION,
                fields=fields,
                evidence=evidence,
            ),
        ]


def _extract_next_data(html: str) -> dict[str, Any] | None:
    match = _NEXT_DATA_RE.search(html)
    if match is None:
        return None
    try:
        data = json.loads(match.group(1))
    except (ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _extract_user(page_props: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the user record out of the first populated timeline entry.

    X's widget puts the rendered tweets at
    ``pageProps.timeline.entries[*].content.tweet.user`` and the same
    user record repeats on every entry. We grab the first one we find;
    the timeline is empty for accounts with no tweets and we fall back
    to ``None`` (header-only trace).
    """
    timeline = _safe_dict(page_props.get("timeline"))
    entries = timeline.get("entries")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        content = _safe_dict(entry.get("content"))
        tweet = _safe_dict(content.get("tweet"))
        user = tweet.get("user")
        if isinstance(user, dict) and user:
            return user
    return None


def _normalise(*, canonical: str, user: dict[str, Any] | None) -> dict[str, Any]:
    profile_url = f"{X_PROFILE_HOST}/{canonical}"
    if user is None:
        # Account exists but has no tweets — header-only trace so the
        # absence of activity itself remains intel.
        return {
            "platform": "x",
            "profile_url": profile_url,
            "screen_name": canonical,
            "user_id": None,
            "display_name": None,
            "bio": None,
            "avatar_url": None,
            "banner_url": None,
            "followers_count": None,
            "friends_count": None,
            "statuses_count": None,
            "created_at": None,
            "verified": False,
            "location": None,
            "url": None,
        }
    return {
        "platform": "x",
        "profile_url": profile_url,
        "screen_name": _str_or_none(user.get("screen_name")) or canonical,
        "user_id": _str_or_none(user.get("id_str")),
        "display_name": _str_or_none(user.get("name")),
        "bio": _str_or_none(user.get("description")),
        "avatar_url": _strip_query(_str_or_none(user.get("profile_image_url_https"))),
        "banner_url": _strip_query(_str_or_none(user.get("profile_banner_url"))),
        "followers_count": _int_or_none(user.get("followers_count")),
        "friends_count": _int_or_none(user.get("friends_count")),
        "statuses_count": _int_or_none(user.get("statuses_count")),
        "created_at": _x_created_to_iso(user.get("created_at")),
        "verified": bool(user.get("verified")),
        "location": _str_or_none(user.get("location")),
        "url": _expand_user_url(user),
    }


def _expand_user_url(user: dict[str, Any]) -> str | None:
    """Prefer the expanded URL X tucks in ``entities.url.urls[0]``.

    X stores user-supplied URLs as t.co shortlinks in ``user.url`` and
    publishes the unshortened version in
    ``user.entities.url.urls[0].expanded_url``. The latter is always
    higher signal for OSINT correlation.
    """
    entities = _safe_dict(user.get("entities"))
    url_block = _safe_dict(entities.get("url"))
    urls = url_block.get("urls")
    if isinstance(urls, list):
        for entry in urls:
            if isinstance(entry, dict):
                expanded = entry.get("expanded_url")
                if isinstance(expanded, str) and expanded:
                    return expanded
    return _str_or_none(user.get("url"))


def _x_created_to_iso(value: Any) -> str | None:
    """Parse X's ``"Tue Mar 21 20:50:14 +0000 2006"`` into ISO 8601."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return None
    return parsed.isoformat()


def _strip_query(url: str | None) -> str | None:
    if url is None:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
