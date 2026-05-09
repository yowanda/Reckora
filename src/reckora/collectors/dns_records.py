"""DNS records collector for ``domain`` identifiers.

This complements the WHOIS / RDAP collector. WHOIS gives the registry view
(registrar, registrant org, dates, status). DNS gives the operational view
(authoritative nameservers, mail exchangers, SPF / DMARC posture, DNSSEC
signing, A / AAAA / CAA records). Together they give the orchestrator and
correlation engine enough signal to reason about shared infrastructure
across domains owned by the same operator without scraping HTML.

The collector resolves a small fixed set of record types via
``dns.asyncresolver.Resolver`` so the orchestrator's asyncio loop is never
blocked on a synchronous DNS call. All record-level errors
(``NXDOMAIN``, ``NoAnswer``, ``Timeout``, ``NoNameservers``) are caught
per-rtype and treated as "no records of that kind" so a partial DNS view
still produces a Trace; only a globally-failing resolver short-circuits to
an empty result list.

Source URL is ``dns://<domain>`` — synthetic, but unique per identifier so
the evidence chain still has a stable URL to anchor on (mirrors the
``phone://libphonenumber/`` convention used by :class:`PhoneCollector`).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import dns.asyncresolver
import dns.exception
import dns.rdatatype
import dns.resolver

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

log = logging.getLogger(__name__)

DNS_SOURCE_URL_PREFIX = "dns://"

# Record types we actively pull at the apex. DMARC lives at
# ``_dmarc.<domain>`` and is fetched via a separate query.
_APEX_RECORD_TYPES: tuple[str, ...] = ("NS", "MX", "TXT", "A", "AAAA", "CAA")


class DNSCollector(Collector):
    """Resolve a small fixed set of DNS records for a domain.

    The collector is deliberately offline-safe in tests: pass a fake
    resolver via the ``resolver`` constructor argument and no real DNS
    traffic is ever generated.

    Parameters
    ----------
    resolver:
        Override the default :class:`dns.asyncresolver.Resolver`. Tests
        pass a stub here; production code can pass a custom resolver
        with pinned upstream servers (e.g. 1.1.1.1).
    timeout:
        Per-query timeout in seconds. Applied via the resolver's
        ``timeout`` attribute.
    lifetime:
        Total lifetime per query (across retries) in seconds. Applied
        via the resolver's ``lifetime`` attribute.
    """

    name: ClassVar[str] = "dns"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.DOMAIN.value})

    def __init__(
        self,
        resolver: Any | None = None,
        *,
        timeout: float = 5.0,
        lifetime: float = 10.0,
    ) -> None:
        super().__init__()
        if resolver is None:
            resolver = dns.asyncresolver.Resolver()
            resolver.timeout = timeout
            resolver.lifetime = lifetime
        self._resolver = resolver

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []

        domain = identifier.value.strip().lower().rstrip(".")
        if not domain:
            return []

        fields: dict[str, Any] = {"domain": domain}
        for rtype in _APEX_RECORD_TYPES:
            fields[f"{rtype.lower()}_records"] = await self._resolve_rrset(domain, rtype)

        # MX: parsed list of {preference, exchange} sorted by preference.
        fields["mx_hosts"] = _parse_mx_records(fields["mx_records"])

        # SPF: extracted from TXT records at the apex.
        fields["spf_record"] = _extract_spf(fields["txt_records"])

        # DMARC: lives at ``_dmarc.<domain>`` as a TXT record.
        dmarc_txts = await self._resolve_rrset(f"_dmarc.{domain}", "TXT")
        fields["dmarc_record"] = _extract_dmarc(dmarc_txts)

        # DNSSEC: presence of a DS record at the apex is the standard
        # external check that the zone is signed and anchored.
        fields["dnssec_signed"] = bool(await self._resolve_rrset(domain, "DS"))

        # If literally nothing resolved, don't emit a Trace — collectors
        # are expected to return [] on a clean miss.
        if not _has_any_record(fields):
            return []

        evidence = make_evidence(
            f"{DNS_SOURCE_URL_PREFIX}{domain}",
            fields,
            keep_raw=False,
        )
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.DNS_RESOLVER,
                fields=fields,
                evidence=evidence,
            ),
        ]

    async def _resolve_rrset(self, qname: str, rtype: str) -> list[str]:
        """Resolve one rtype for one qname; return a list of textual rdata.

        Per-rtype errors are swallowed and logged at DEBUG level — a
        domain is allowed to lack MX records or DNSSEC and still produce
        a useful Trace. Only NoNameservers at the global level surfaces
        as a transport error.
        """
        try:
            answer = await self._resolver.resolve(qname, rtype)
        except (
            dns.resolver.NXDOMAIN,
            dns.resolver.NoAnswer,
            dns.resolver.NoNameservers,
            dns.exception.Timeout,
        ) as exc:
            log.debug("dns: %s %s -> %s", qname, rtype, type(exc).__name__)
            return []
        except dns.exception.DNSException as exc:  # defensive net
            log.debug("dns: %s %s -> %s: %s", qname, rtype, type(exc).__name__, exc)
            return []

        out: list[str] = []
        for rdata in answer:
            text = rdata.to_text()
            # TXT rdata renders with surrounding quotes per RFC; strip
            # them so consumers don't have to.
            if rtype == "TXT":
                text = _unquote_txt(text)
            out.append(text)
        return out


def _unquote_txt(text: str) -> str:
    """Strip the matched leading / trailing double-quote pair that
    ``dnspython`` adds when rendering a TXT rdata."""
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1]
    return text


def _parse_mx_records(raw: list[str]) -> list[dict[str, Any]]:
    """Parse ``"10 mail.example.com."`` strings into structured hosts.

    Sorted by preference ascending so the primary MX is index 0. Hosts
    are lowercased and trailing dots stripped so they compare cleanly
    across registrars that include / omit the FQDN root dot.
    """
    parsed: list[dict[str, Any]] = []
    for entry in raw:
        parts = entry.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pref = int(parts[0])
        except ValueError:
            continue
        host = parts[1].strip().lower().rstrip(".")
        if not host:
            continue
        parsed.append({"preference": pref, "exchange": host})
    parsed.sort(key=lambda mx: (mx["preference"], mx["exchange"]))
    return parsed


def _extract_spf(txt_records: list[str]) -> str | None:
    """Return the first ``v=spf1...`` TXT record, if any.

    Only one valid SPF record is allowed per RFC 7208 §3.2; we still
    walk the whole TXT list so a domain with multiple TXT records
    (common for verification tokens) is handled correctly.
    """
    for txt in txt_records:
        if txt.lower().startswith("v=spf1"):
            return txt
    return None


def _extract_dmarc(dmarc_txts: list[str]) -> str | None:
    """Return the first ``v=DMARC1...`` TXT record at ``_dmarc.<d>``."""
    for txt in dmarc_txts:
        if txt.lower().startswith("v=dmarc1"):
            return txt
    return None


def _has_any_record(fields: dict[str, Any]) -> bool:
    """True iff at least one record-bearing field is non-empty."""
    for key, value in fields.items():
        if key == "domain":
            continue
        if isinstance(value, list) and value:
            return True
        if isinstance(value, str) and value:
            return True
        if value is True:
            return True
    return False
