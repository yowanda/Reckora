"""Breach lookup collector backed by the Have I Been Pwned (HIBP) API v3.

`HIBP <https://haveibeenpwned.com/API/v3>`_ is a curated, paid index of public
data breaches keyed by email address. Compared with our other collectors it
has three important properties:

* It is **paid** — the ``hibp-api-key`` header is mandatory; without one the
  endpoint always 401s. The collector therefore short-circuits to an empty
  trace list when no key is provided, matching the "best-effort, opt-in"
  contract used by the archive / screenshot pipelines.
* The response can be **PII-heavy** (a list of where an email has been
  breached, plus the kind of data that leaked). We therefore drop the inline
  raw payload (``keep_raw=False``) — the SHA-256 of the canonicalised
  response is preserved in evidence so the chain stays auditable, but the
  actual breach metadata only shows up in the normalised ``Trace.fields``
  schema we publish below.
* It is feature-flagged at **two layers** — the collector is only added to
  the orchestrator when the caller passes ``--breach`` (CLI) or
  ``breach: true`` (HTTP). Even if it is added, the lack of an API key turns
  it into a no-op. This makes it impossible to leak HIBP queries for users
  who never opted in.

The normalised ``Trace.fields`` schema is intentionally flat so the
correlation engine and the dossier renderers can pick the high-signal bits
without parsing nested arrays at render time:

- ``email`` — canonicalised lowercase form of the input identifier
- ``breach_count`` — total number of breaches the account is in
- ``first_breach_date`` / ``latest_breach_date`` — ISO 8601 date strings,
  inclusive on both ends; ``None`` when the account has no breaches
- ``data_classes`` — sorted, de-duplicated union of the leaked data
  categories across every breach (e.g. ``["Email addresses", "Passwords"]``)
- ``has_sensitive_breach`` — ``True`` when at least one HIBP-flagged
  "sensitive" breach is present (porn / addiction / health sites etc.;
  HIBP exposes these only with explicit consent)
- ``breaches`` — per-breach summary list, sorted ascending by ``breach_date``
  (then ``name``) for deterministic output, with the high-signal fields
  HIBP itself documents.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

HIBP_API_BASE = "https://haveibeenpwned.com/api/v3"


class BreachCollector(Collector):
    """Collect HIBP breach exposure for an ``email`` identifier.

    Parameters
    ----------
    api_key:
        HIBP API key (``hibp-api-key`` header). When ``None`` the collector
        is a no-op so investigations on hosts without a key still complete
        without errors.
    user_agent:
        HIBP requires a descriptive ``User-Agent`` per their docs. Defaults
        to ``"Reckora/0.1"`` to match the rest of the engine.
    base_url:
        Override for tests; defaults to the production HIBP host.
    """

    name: ClassVar[str] = "breach_hibp"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.EMAIL.value})

    def __init__(
        self,
        *,
        api_key: str | None,
        client: httpx.AsyncClient | None = None,
        user_agent: str = "Reckora/0.1",
        base_url: str = HIBP_API_BASE,
    ) -> None:
        super().__init__(client)
        self._api_key = api_key
        self._user_agent = user_agent
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        # ``hibp-api-key`` MUST be present per the v3 docs; we only ever set
        # this header when we actually have a key, so callers can read the
        # absence of the header as "do not call HIBP".
        return {
            "hibp-api-key": self._api_key or "",
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        }

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        if not self._api_key:
            # Feature-flag layer two: no key = no network call. Investigations
            # remain deterministic on hosts that never set ``HIBP_API_KEY``.
            return []

        email = identifier.value.strip().lower()
        client = await self._http()
        url = (
            f"{self._base_url}/breachedaccount/{email}"
            f"?truncateResponse=false&includeUnverified=true"
        )
        resp = await client.get(url, headers=self._headers())

        # 404 is the documented "no breaches" response, NOT an error.
        if resp.status_code == 404:
            return [self._clean_trace(identifier=identifier, email=email, url=url)]
        # 401 (bad key), 429 (rate limit) etc. are operational failures —
        # surface them so the orchestrator's per-collector try/except logs
        # them once instead of swallowing silently in a way that would mask
        # a misconfigured key.
        resp.raise_for_status()

        breaches_raw = resp.json()
        if not isinstance(breaches_raw, list):
            return [self._clean_trace(identifier=identifier, email=email, url=url)]

        fields = self._normalise(email=email, breaches=breaches_raw)
        evidence = make_evidence(url, {"breaches": breaches_raw}, keep_raw=False)
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.BREACH_HIBP,
                fields=fields,
                evidence=evidence,
            ),
        ]

    @staticmethod
    def _clean_trace(*, identifier: Identifier, email: str, url: str) -> Trace:
        """Return the "no breaches" Trace.

        We still emit a trace (rather than ``[]``) so that a clean account
        is visible in the dossier — that is itself a useful intelligence
        finding, not the absence of one.
        """
        fields = {
            "email": email,
            "breach_count": 0,
            "first_breach_date": None,
            "latest_breach_date": None,
            "data_classes": [],
            "has_sensitive_breach": False,
            "breaches": [],
        }
        evidence = make_evidence(url, {"breaches": []}, keep_raw=False)
        return Trace(
            identifier=identifier,
            source=TraceSource.BREACH_HIBP,
            fields=fields,
            evidence=evidence,
        )

    @staticmethod
    def _normalise(*, email: str, breaches: list[Any]) -> dict[str, Any]:
        clean: list[dict[str, Any]] = []
        data_classes: set[str] = set()
        has_sensitive = False
        for raw in breaches:
            if not isinstance(raw, dict):
                continue
            classes = [c for c in (raw.get("DataClasses") or []) if isinstance(c, str)]
            data_classes.update(classes)
            if bool(raw.get("IsSensitive")):
                has_sensitive = True
            clean.append(
                {
                    "name": raw.get("Name"),
                    "title": raw.get("Title"),
                    "domain": raw.get("Domain"),
                    "breach_date": raw.get("BreachDate"),
                    "added_date": raw.get("AddedDate"),
                    "pwn_count": raw.get("PwnCount"),
                    "data_classes": sorted(classes),
                    "is_verified": bool(raw.get("IsVerified")),
                    "is_fabricated": bool(raw.get("IsFabricated")),
                    "is_sensitive": bool(raw.get("IsSensitive")),
                    "is_retired": bool(raw.get("IsRetired")),
                    "is_spam_list": bool(raw.get("IsSpamList")),
                }
            )

        # Deterministic ordering so the dossier and the SHA-256 of the
        # canonicalised payload don't drift between runs even if HIBP
        # changes its response order.
        clean.sort(key=lambda b: ((b.get("breach_date") or ""), (b.get("name") or "")))

        breach_dates = [b["breach_date"] for b in clean if b.get("breach_date")]
        return {
            "email": email,
            "breach_count": len(clean),
            "first_breach_date": breach_dates[0] if breach_dates else None,
            "latest_breach_date": breach_dates[-1] if breach_dates else None,
            "data_classes": sorted(data_classes),
            "has_sensitive_breach": has_sensitive,
            "breaches": clean,
        }
