"""Reddit user-profile collector — public ``/about.json`` endpoint.

Reddit ships an unauthenticated JSON view of every public account at
``https://www.reddit.com/user/{username}/about.json``. The shape mirrors
the website but with an extra wrapper:

.. code-block:: text

    {"kind": "t2", "data": {<actual fields>}}

The endpoint requires a non-default ``User-Agent`` (Reddit returns 429
otherwise) but no authentication and no API key. We therefore wire this
collector into the default investigation set rather than gating it
behind a feature flag — Phase 3 already wires GitHub and the WHOIS /
RDAP collectors for ``username`` / ``domain`` identifiers; this one
covers the third platform users are most likely to seed Reckora with.

The normalised :pyattr:`reckora.models.entity.Trace.fields` schema:

- ``platform`` — always ``"reddit"`` (so cross-platform correlation can
  filter on the field rather than the trace source enum)
- ``profile_url`` — ``https://www.reddit.com/user/{name}/`` (canonical;
  Reddit accepts ``/u/`` as an alias but we normalise to the long form)
- ``display_name`` — ``subreddit.title`` (Reddit calls this the "display
  name", distinct from the URL slug)
- ``bio`` — ``subreddit.public_description`` (the user-editable blurb on
  their profile page)
- ``avatar_url`` — first non-``None`` of ``icon_img`` /
  ``subreddit.icon_img`` / ``subreddit.banner_img`` (querystring stripped
  so the URL is stable across requests)
- ``link_karma`` / ``comment_karma`` / ``total_karma`` — ints, never
  ``None`` (Reddit always reports these for live accounts)
- ``created_utc`` — ISO 8601 UTC string (Reddit publishes a unix-epoch
  float; we render it for the dossier)
- ``has_verified_email`` / ``is_employee`` / ``is_gold`` / ``is_mod`` —
  booleans, the high-signal flags Reddit exposes on every account
- ``is_suspended`` — ``True`` for suspended accounts (Reddit returns
  ``{"is_suspended": true}`` and omits most other fields)

The raw Reddit envelope is **not** kept inline — Reddit's account JSON
includes the user's avatar URL twice, the icon URL three times, and a
preferences blob we never look at. The ``Evidence.payload_sha256`` is
preserved so the chain stays auditable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

REDDIT_API_BASE = "https://www.reddit.com"


class RedditCollector(Collector):
    """Collect a public Reddit profile via ``/user/{name}/about.json``.

    Parameters
    ----------
    client:
        Optional httpx client; tests inject a client wired to
        ``pytest-httpx``.
    user_agent:
        Reddit refuses default ``python-httpx/...`` UAs. Defaults to a
        descriptive string; callers can override per the Reddit API
        guidelines (``<platform>:<app-id>:<version> (by /u/<name>)``).
    base_url:
        Override for tests; defaults to the public Reddit host.
    """

    name: ClassVar[str] = "reddit_profile"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.USERNAME.value})

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        user_agent: str = "reckora:osint-investigation:0.1 (+https://github.com/yowanda/Reckora)",
        base_url: str = REDDIT_API_BASE,
    ) -> None:
        super().__init__(client)
        self._user_agent = user_agent
        self._base_url = base_url.rstrip("/")

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        # Reddit usernames are case-insensitive but the canonical URL
        # uses the lowercased form.
        username = identifier.value.strip()
        if not username:
            return []
        url = f"{self._base_url}/user/{username}/about.json"
        client = await self._http()
        resp = await client.get(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": self._user_agent,
            },
        )
        # 404 = unknown account, 451 / 403 = takedown / shadowbanned —
        # all of those are "no profile to render", not transport errors.
        if resp.status_code in (403, 404, 451):
            return []
        resp.raise_for_status()
        try:
            envelope = resp.json()
        except ValueError:
            return []
        if not isinstance(envelope, dict):
            return []
        data = envelope.get("data")
        if not isinstance(data, dict):
            return []
        fields = _normalise(username=username, data=data)
        evidence = make_evidence(url, envelope, keep_raw=False)
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.REDDIT_PROFILE,
                fields=fields,
                evidence=evidence,
            ),
        ]


def _normalise(*, username: str, data: dict[str, Any]) -> dict[str, Any]:
    subreddit = data.get("subreddit") if isinstance(data.get("subreddit"), dict) else {}
    assert isinstance(subreddit, dict)  # narrows for type-checker

    if data.get("is_suspended"):
        # Reddit omits most fields on suspended accounts; emit a terse
        # row that the dossier can render without hitting KeyErrors.
        return {
            "platform": "reddit",
            "profile_url": f"https://www.reddit.com/user/{username}/",
            "display_name": None,
            "bio": None,
            "avatar_url": None,
            "link_karma": None,
            "comment_karma": None,
            "total_karma": None,
            "created_utc": None,
            "has_verified_email": False,
            "is_employee": False,
            "is_gold": False,
            "is_mod": False,
            "is_suspended": True,
        }

    avatar_url = _first_str(
        data.get("icon_img"),
        subreddit.get("icon_img"),
        subreddit.get("banner_img"),
    )

    return {
        "platform": "reddit",
        "profile_url": f"https://www.reddit.com/user/{username}/",
        "display_name": _str_or_none(subreddit.get("title")),
        "bio": _str_or_none(subreddit.get("public_description")),
        "avatar_url": avatar_url,
        "link_karma": _int_or_none(data.get("link_karma")),
        "comment_karma": _int_or_none(data.get("comment_karma")),
        "total_karma": _int_or_none(data.get("total_karma")),
        "created_utc": _epoch_to_iso(data.get("created_utc")),
        "has_verified_email": bool(data.get("has_verified_email")),
        "is_employee": bool(data.get("is_employee")),
        "is_gold": bool(data.get("is_gold")),
        "is_mod": bool(data.get("is_mod")),
        "is_suspended": False,
    }


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _epoch_to_iso(value: Any) -> str | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    return datetime.fromtimestamp(float(value), tz=UTC).isoformat()


def _first_str(*candidates: Any) -> str | None:
    """Return the first non-empty string with its querystring stripped.

    Reddit's avatar / icon URLs include cache-busting querystrings that
    rotate on every request; stripping them keeps the trace stable
    across repeated investigations of the same user.
    """
    for c in candidates:
        if isinstance(c, str) and c:
            parts = urlsplit(c)
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return None
