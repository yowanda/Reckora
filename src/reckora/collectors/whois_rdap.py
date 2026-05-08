"""WHOIS / RDAP collector for `domain` Identifiers.

Uses `rdap.org` as a generic bootstrap that redirects to the right authoritative
RDAP server for the TLD. The result is normalised into a small flat dict with
the high-signal fields a correlation engine actually wants:

- registrar
- registrant_org
- created_at / updated_at / expires_at (ISO strings)
- nameservers
- status

We deliberately drop the rest of the RDAP envelope; the SHA-256 of the full
response is preserved in evidence so the rest is recoverable on demand.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

RDAP_DOMAIN_BASE = "https://rdap.org/domain"


class WhoisRdapCollector(Collector):
    """Collect WHOIS-equivalent data from a public RDAP server."""

    name: ClassVar[str] = "whois_rdap"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.DOMAIN.value})

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        client = await self._http()
        url = f"{RDAP_DOMAIN_BASE}/{identifier.value}"
        headers = {
            "Accept": "application/rdap+json",
            "User-Agent": "Reckora/0.1",
        }
        resp = await client.get(url, headers=headers)
        if resp.status_code in (404, 400):
            return []
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        fields = self._normalise(data)
        evidence = make_evidence(url, data, keep_raw=False)
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.WHOIS_RDAP,
                fields=fields,
                evidence=evidence,
            ),
        ]

    @staticmethod
    def _normalise(data: dict[str, Any]) -> dict[str, Any]:
        events = {
            e.get("eventAction"): e.get("eventDate")
            for e in (data.get("events") or [])
            if isinstance(e, dict)
        }
        registrant_org = WhoisRdapCollector._extract_registrant_org(data)
        registrar = WhoisRdapCollector._extract_registrar(data)
        nameservers: list[str] = []
        for ns in data.get("nameservers") or []:
            if not isinstance(ns, dict):
                continue
            ldh = ns.get("ldhName")
            if isinstance(ldh, str):
                nameservers.append(ldh.lower())
        return {
            "domain": data.get("ldhName"),
            "registrar": registrar,
            "registrant_org": registrant_org,
            "created_at": events.get("registration"),
            "updated_at": events.get("last changed"),
            "expires_at": events.get("expiration"),
            "nameservers": nameservers,
            "status": list(data.get("status") or []),
        }

    @staticmethod
    def _extract_registrant_org(data: dict[str, Any]) -> str | None:
        return WhoisRdapCollector._extract_vcard_field(data, "registrant", "org")

    @staticmethod
    def _extract_registrar(data: dict[str, Any]) -> str | None:
        return WhoisRdapCollector._extract_vcard_field(data, "registrar", "fn")

    @staticmethod
    def _extract_vcard_field(data: dict[str, Any], role: str, prop_name: str) -> str | None:
        for ent in data.get("entities") or []:
            if not isinstance(ent, dict):
                continue
            if role not in (ent.get("roles") or []):
                continue
            vcard = ent.get("vcardArray")
            if not (isinstance(vcard, list) and len(vcard) == 2):
                continue
            props = vcard[1]
            if not isinstance(props, list):
                continue
            for prop in props:
                if (
                    isinstance(prop, list)
                    and len(prop) >= 4
                    and prop[0] == prop_name
                    and isinstance(prop[3], str)
                ):
                    return prop[3]
        return None
