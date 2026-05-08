"""Email-profile collector — syntax + MX + public Gravatar profile.

Most of what we know about an email address is *publicly* knowable without
an API key, and yet it's high-signal for the entity-resolution work the
correlator does:

* the **local-part** and **domain** are part of the canonical identifier;
* whether the **MX records** resolve tells us whether the domain can
  receive mail at all (a useful "is this a throwaway address?" signal);
* a **Gravatar** profile, when one exists, links the email to a
  ``display_name`` / ``about`` / ``profile_url`` triple keyed on the
  ``MD5(lower(email))`` hash that Gravatar publishes itself.

We deliberately do **not** try to enumerate which big mail providers a
domain belongs to (Gmail / Outlook / iCloud / etc.) — that's a job for a
downstream rule, not a collector. Likewise, we never call HIBP from here
— the dedicated ``BreachCollector`` is feature-flagged, this one isn't.

The normalised :pyattr:`reckora.models.entity.Trace.fields` schema:

- ``email`` — lowercase, stripped form of the input identifier
- ``local_part`` — the bit before the ``@``
- ``domain`` — the bit after the ``@`` (lowercased)
- ``mx_resolved`` — ``True`` iff ``domain`` has at least one MX record
- ``mx_hosts`` — sorted, de-duplicated list of MX hostnames (lowercase,
  trailing dot stripped) — empty when ``mx_resolved`` is ``False``
- ``has_gravatar`` — ``True`` iff Gravatar served a profile for the email
- ``gravatar_url`` — ``profileUrl`` from the Gravatar JSON, ``None``
  otherwise
- ``gravatar_display_name`` / ``gravatar_about`` / ``gravatar_location``
  — the high-signal profile fields, ``None`` when absent

The Gravatar response is content-hashed into ``Evidence`` but the raw
payload is dropped (``keep_raw=False``) — Gravatar profiles can carry
PII like phone numbers and we don't want to inline that into the dossier.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, ClassVar

import dns.asyncresolver
import dns.exception
import httpx

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

# RFC 5322 in full is famously baroque; for our purposes the goal is to
# reject obvious garbage (no ``@``, empty local-part, whitespace, etc.)
# rather than be a strict validator. The :func:`validate_syntax` helper
# is also exported so the API layer can short-circuit obvious mistakes
# before they hit the orchestrator.
_EMAIL_SYNTAX_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

GRAVATAR_API_BASE = "https://www.gravatar.com"


class EmailCollector(Collector):
    """Collect public email metadata: syntax + MX + Gravatar profile.

    Parameters
    ----------
    client:
        Optional httpx client; we use one for the Gravatar lookup. Tests
        inject a client wired to ``pytest-httpx``.
    resolver:
        Optional :class:`dns.asyncresolver.Resolver` for the MX lookup.
        Tests inject a stub resolver. When ``None`` we build a default
        one with a 5 s lifetime so a hung DNS server can't wedge an
        investigation indefinitely.
    user_agent:
        Sent to Gravatar; defaults to ``Reckora/0.1`` for parity with
        the rest of the engine.
    timeout:
        DNS resolver timeout, in seconds. Applied per query.
    base_url:
        Override for tests; defaults to the public Gravatar host.
    """

    name: ClassVar[str] = "email_profile"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.EMAIL.value})

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        resolver: dns.asyncresolver.Resolver | None = None,
        user_agent: str = "Reckora/0.1",
        timeout: float = 5.0,
        base_url: str = GRAVATAR_API_BASE,
    ) -> None:
        super().__init__(client)
        self._resolver = resolver
        self._user_agent = user_agent
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        email = identifier.value.strip().lower()
        if not validate_syntax(email):
            # An invalid-shaped email is a noteworthy finding (the seed
            # itself is suspect) but we still don't want to issue
            # network calls on garbage input.
            return [self._invalid_trace(identifier=identifier, email=email)]

        local_part, _, domain = email.partition("@")
        mx_hosts = await self._lookup_mx(domain)
        gravatar_url, gravatar_data = await self._fetch_gravatar(email)

        fields = _normalise(
            email=email,
            local_part=local_part,
            domain=domain,
            mx_hosts=mx_hosts,
            gravatar_data=gravatar_data,
        )
        # Gravatar is the only network source we keep an evidence row
        # for; the MX lookup is recorded as a derived ``mx_resolved``
        # boolean rather than a separate trace because DNS responses
        # don't have a stable "url" to anchor an Evidence to.
        evidence = make_evidence(gravatar_url, gravatar_data or {}, keep_raw=False)
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.EMAIL_PROFILE,
                fields=fields,
                evidence=evidence,
            ),
        ]

    async def _lookup_mx(self, domain: str) -> list[str]:
        """Return MX hostnames for ``domain``, sorted asc.

        Empty list on NXDOMAIN, no answer, or any DNS error — none of
        those are fatal to the collector, they're just signal.
        """
        resolver = self._resolver or _default_resolver(self._timeout)
        try:
            answer = await resolver.resolve(domain, "MX")
        except dns.exception.DNSException:
            return []
        hosts: set[str] = set()
        for rdata in answer:
            # ``rdata.exchange`` is a ``dns.name.Name``; ``to_text``
            # adds a trailing dot we drop for human-friendly output.
            host = str(rdata.exchange.to_text()).rstrip(".").lower()
            if host:
                hosts.add(host)
        return sorted(hosts)

    async def _fetch_gravatar(self, email: str) -> tuple[str, dict[str, Any] | None]:
        """Fetch the Gravatar profile JSON for ``email``.

        Returns ``(profile_url, parsed_json | None)``. ``None`` payload
        means Gravatar replied with 404 (no profile) or returned a non-
        JSON body. Network errors propagate so the orchestrator can log
        them; that mirrors how every other collector handles transport.
        """
        digest = hashlib.md5(email.encode("utf-8"), usedforsecurity=False).hexdigest()
        profile_url = f"{self._base_url}/{digest}.json"
        client = await self._http()
        resp = await client.get(
            profile_url,
            headers={
                "Accept": "application/json",
                "User-Agent": self._user_agent,
            },
        )
        if resp.status_code == 404:
            return profile_url, None
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            return profile_url, None
        if not isinstance(data, dict):
            return profile_url, None
        return profile_url, data

    @staticmethod
    def _invalid_trace(*, identifier: Identifier, email: str) -> Trace:
        """Trace for a syntactically-invalid email — no network calls."""
        local_part, _, domain = email.partition("@")
        fields: dict[str, Any] = {
            "email": email,
            "local_part": local_part,
            "domain": domain,
            "syntax_valid": False,
            "mx_resolved": False,
            "mx_hosts": [],
            "has_gravatar": False,
            "gravatar_url": None,
            "gravatar_display_name": None,
            "gravatar_about": None,
            "gravatar_location": None,
        }
        # No source URL exists for an invalid email; we anchor the
        # evidence at a sentinel so the chain still has a payload hash.
        evidence = make_evidence(
            f"reckora://invalid-email/{email}",
            {"reason": "syntax_invalid"},
            keep_raw=False,
        )
        return Trace(
            identifier=identifier,
            source=TraceSource.EMAIL_PROFILE,
            fields=fields,
            evidence=evidence,
        )


def validate_syntax(email: str) -> bool:
    """Cheap RFC-ish syntax check.

    Public so the API layer can reject malformed seeds before queuing
    an investigation, without re-implementing the regex.
    """
    return _EMAIL_SYNTAX_RE.match(email) is not None


def _default_resolver(timeout: float) -> dns.asyncresolver.Resolver:
    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = timeout
    resolver.lifetime = timeout
    return resolver


def _normalise(
    *,
    email: str,
    local_part: str,
    domain: str,
    mx_hosts: list[str],
    gravatar_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Flatten Gravatar's nested ``entry[]`` shape into the trace fields.

    Gravatar wraps the actual profile in ``{"entry": [<profile>]}``; we
    pluck the first entry and lift the high-signal fields up so the
    correlation engine doesn't have to know about the wire shape.
    """
    profile: dict[str, Any] | None = None
    if gravatar_data is not None:
        entries = gravatar_data.get("entry")
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            profile = entries[0]

    def _str_or_none(key: str) -> str | None:
        if profile is None:
            return None
        v = profile.get(key)
        return v if isinstance(v, str) and v else None

    return {
        "email": email,
        "local_part": local_part,
        "domain": domain,
        "syntax_valid": True,
        "mx_resolved": bool(mx_hosts),
        "mx_hosts": mx_hosts,
        "has_gravatar": profile is not None,
        "gravatar_url": _str_or_none("profileUrl"),
        "gravatar_display_name": _str_or_none("displayName"),
        "gravatar_about": _str_or_none("aboutMe"),
        "gravatar_location": _str_or_none("currentLocation"),
    }
