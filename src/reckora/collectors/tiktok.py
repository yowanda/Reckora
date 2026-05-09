"""TikTok collector — public profile page rehydration blob.

TikTok renders every public profile at ``https://www.tiktok.com/@<u>``.
The HTML always carries a self-contained
``<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">`` JSON payload that
the SPA reads on load to populate the page. That payload contains the
canonical user record TikTok itself trusts:

.. code-block:: text

    __DEFAULT_SCOPE__["webapp.user-detail"] = {
        "statusCode": 0,
        "userInfo": {
            "user":  { "uniqueId": ..., "nickname": ..., "signature": ...,
                       "secUid": ..., "createTime": ..., "verified": ...,
                       "avatarLarger": ..., "region": ... },
            "stats": { "followerCount", "followingCount",
                       "videoCount", "heartCount" }
        }
    }

Existence signal is in the same blob: TikTok stamps ``statusCode == 0``
on real accounts and ``statusCode == 10221`` for "user couldn't be
found". We treat any non-zero ``statusCode`` as a miss and return an
empty list — collection misses, not transport errors.

The fetch is unauthenticated; TikTok does occasionally rate-limit
data-center IPs with HTTP 403 / 429. In that case we surface the
``HTTPStatusError`` so the orchestrator's retry / log layer sees it,
matching the pattern Reddit / Hacker News collectors use for the same
class of failure.

The normalised :pyattr:`reckora.models.entity.Trace.fields` schema:

- ``platform`` — always ``"tiktok"``
- ``profile_url`` — ``https://www.tiktok.com/@<uniqueId>``
- ``unique_id`` — the canonical handle TikTok echoed back
- ``sec_uid`` — TikTok's internal stable user id (long opaque string)
- ``user_id`` — TikTok's numeric user id (kept alongside ``sec_uid``)
- ``display_name`` — ``user.nickname``
- ``bio`` — ``user.signature``
- ``avatar_url`` — ``user.avatarLarger`` (querystring stripped so the
  CDN signing parameters don't pollute the dossier)
- ``verified`` — ``user.verified`` (boolean)
- ``private_account`` — ``user.privateAccount`` (boolean)
- ``region`` — ``user.region`` (ISO-3166 alpha-2) or ``None``
- ``created_at`` — ISO 8601 string (TikTok publishes a unix-epoch int)
- ``followers_count`` / ``following_count`` / ``video_count`` /
  ``heart_count`` — ints or ``None``

The full HTML page is **not** kept inline — it's ~280 KB of SPA bundle.
Only the parsed user-detail slice is hashed for the evidence chain.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

TIKTOK_PROFILE_BASE = "https://www.tiktok.com"
# TikTok uniqueIds are 2-24 ASCII alphanumerics + underscore + period.
_TIKTOK_HANDLE_RE = re.compile(r"^[A-Za-z0-9_.]{2,24}$")
_REHYDRATION_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.+?)</script>',
    re.DOTALL,
)
_USER_DETAIL_KEY = "webapp.user-detail"


class TikTokCollector(Collector):
    """Collect a public TikTok profile via the SPA rehydration payload."""

    name: ClassVar[str] = "tiktok"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.USERNAME.value})

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        base_url: str = TIKTOK_PROFILE_BASE,
    ) -> None:
        super().__init__(client)
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
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
        if not handle or not _TIKTOK_HANDLE_RE.match(handle):
            return []
        url = f"{self._base_url}/@{handle}"
        client = await self._http()
        resp = await client.get(url, headers=self._headers())
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        rehydration = _extract_rehydration(resp.text)
        if rehydration is None:
            return []
        scope = _safe_dict(rehydration.get("__DEFAULT_SCOPE__"))
        user_detail = _safe_dict(scope.get(_USER_DETAIL_KEY))
        # statusCode != 0 means TikTok refused to render a profile.
        # 10221 is the canonical "user not found"; treat any non-zero
        # value as "no profile" so future error codes don't leak through.
        status_code = user_detail.get("statusCode")
        if not isinstance(status_code, int) or status_code != 0:
            return []
        user_info = _safe_dict(user_detail.get("userInfo"))
        user = _safe_dict(user_info.get("user"))
        if not user.get("uniqueId"):
            return []
        stats = _safe_dict(user_info.get("stats"))
        fields = _normalise(user=user, stats=stats)
        evidence_payload: dict[str, Any] = {"user": user, "stats": stats}
        evidence = make_evidence(url, evidence_payload, keep_raw=False)
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.TIKTOK_WEB,
                fields=fields,
                evidence=evidence,
            ),
        ]


def _extract_rehydration(html: str) -> dict[str, Any] | None:
    match = _REHYDRATION_RE.search(html)
    if match is None:
        return None
    try:
        data = json.loads(match.group(1))
    except (ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _normalise(*, user: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    unique_id = _str_or_none(user.get("uniqueId")) or ""
    return {
        "platform": "tiktok",
        "profile_url": f"{TIKTOK_PROFILE_BASE}/@{unique_id}",
        "unique_id": unique_id,
        "sec_uid": _str_or_none(user.get("secUid")),
        "user_id": _str_or_none(user.get("id")),
        "display_name": _str_or_none(user.get("nickname")),
        "bio": _str_or_none(user.get("signature")),
        "avatar_url": _strip_query(_str_or_none(user.get("avatarLarger"))),
        "verified": bool(user.get("verified")),
        "private_account": bool(user.get("privateAccount")),
        "region": _str_or_none(user.get("region")),
        "created_at": _epoch_to_iso(user.get("createTime")),
        "followers_count": _int_or_none(stats.get("followerCount")),
        "following_count": _int_or_none(stats.get("followingCount")),
        "video_count": _int_or_none(stats.get("videoCount")),
        "heart_count": _int_or_none(stats.get("heartCount")),
    }


def _epoch_to_iso(value: Any) -> str | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


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
