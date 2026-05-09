"""Tests for the DNS records collector.

The collector accepts an injected resolver, so these tests run entirely
offline — no real DNS traffic is generated. The fakes mimic the small
slice of the dnspython API surface we depend on:

* ``Resolver.resolve(qname, rdtype)`` is a coroutine.
* The return value is iterable; each rdata has ``to_text()``.
* Errors raise the canonical dnspython exception classes
  (``NXDOMAIN`` / ``NoAnswer`` / ``NoNameservers`` / ``Timeout``).
"""

from __future__ import annotations

from typing import Any

import dns.exception
import dns.resolver
import pytest

from reckora.collectors.dns_records import (
    DNS_SOURCE_URL_PREFIX,
    DNSCollector,
)
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource


class _FakeRdata:
    """Mimic the ``rdata`` records dnspython yields when iterating an Answer."""

    def __init__(self, text: str) -> None:
        self._text = text

    def to_text(self) -> str:
        return self._text


class _FakeAnswer:
    """Iterable replacement for ``dns.resolver.Answer``."""

    def __init__(self, rdata: list[str]) -> None:
        self._rdata = [_FakeRdata(s) for s in rdata]

    def __iter__(self) -> Any:
        return iter(self._rdata)


class _FakeResolver:
    """Minimal stand-in for :class:`dns.asyncresolver.Resolver`.

    ``records`` maps ``(qname, rtype)`` to either a list of textual rdata
    or an exception class to raise.
    """

    def __init__(
        self,
        records: dict[tuple[str, str], list[str] | type[BaseException]],
    ) -> None:
        self._records = records
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, qname: str, rdtype: str) -> _FakeAnswer:
        self.calls.append((qname, rdtype))
        result = self._records.get((qname, rdtype))
        if result is None:
            raise dns.resolver.NoAnswer
        if isinstance(result, type) and issubclass(result, BaseException):
            raise result
        assert isinstance(result, list)
        return _FakeAnswer(result)


def _identifier(value: str = "example.com") -> Identifier:
    return Identifier(type=IdentifierType.DOMAIN, value=value)


async def test_skips_unsupported_identifier_type() -> None:
    collector = DNSCollector(resolver=_FakeResolver({}))
    ident = Identifier(type=IdentifierType.USERNAME, value="alice")
    assert await collector.collect(ident) == []


async def test_returns_empty_when_no_records_resolve() -> None:
    """Domain with NXDOMAIN on every rtype yields no Trace at all."""
    resolver = _FakeResolver({})  # every (qname, rtype) -> NoAnswer
    collector = DNSCollector(resolver=resolver)
    assert await collector.collect(_identifier("nothing.test")) == []


async def test_collects_full_record_set() -> None:
    resolver = _FakeResolver(
        {
            ("example.com", "NS"): ["ns1.example.com.", "ns2.example.com."],
            ("example.com", "MX"): [
                "20 alt.mail.example.com.",
                "10 mail.example.com.",
            ],
            ("example.com", "TXT"): [
                '"v=spf1 include:_spf.google.com ~all"',
                '"google-site-verification=abc123"',
            ],
            ("example.com", "A"): ["93.184.216.34"],
            ("example.com", "AAAA"): ["2606:2800:220:1:248:1893:25c8:1946"],
            ("example.com", "CAA"): ['0 issue "letsencrypt.org"'],
            ("_dmarc.example.com", "TXT"): [
                '"v=DMARC1; p=reject; rua=mailto:postmaster@example.com"',
            ],
            ("example.com", "DS"): ["12345 13 2 a1b2c3..."],
        }
    )
    collector = DNSCollector(resolver=resolver)
    traces = await collector.collect(_identifier())

    assert len(traces) == 1
    trace = traces[0]
    assert trace.source == TraceSource.DNS_RESOLVER
    assert trace.identifier == _identifier()
    assert trace.evidence.source_url == f"{DNS_SOURCE_URL_PREFIX}example.com"

    fields = trace.fields
    assert fields["domain"] == "example.com"
    assert fields["ns_records"] == ["ns1.example.com.", "ns2.example.com."]
    # MX raw preserved + structured / sorted variant present.
    assert fields["mx_records"] == [
        "20 alt.mail.example.com.",
        "10 mail.example.com.",
    ]
    assert fields["mx_hosts"] == [
        {"preference": 10, "exchange": "mail.example.com"},
        {"preference": 20, "exchange": "alt.mail.example.com"},
    ]
    # TXT records: surrounding quotes stripped; SPF extracted.
    assert fields["txt_records"] == [
        "v=spf1 include:_spf.google.com ~all",
        "google-site-verification=abc123",
    ]
    assert fields["spf_record"] == "v=spf1 include:_spf.google.com ~all"
    assert fields["a_records"] == ["93.184.216.34"]
    assert fields["aaaa_records"] == ["2606:2800:220:1:248:1893:25c8:1946"]
    assert fields["caa_records"] == ['0 issue "letsencrypt.org"']
    # DMARC pulled from the _dmarc subdomain.
    assert fields["dmarc_record"] == "v=DMARC1; p=reject; rua=mailto:postmaster@example.com"
    # DNSSEC presence inferred from a non-empty DS rrset.
    assert fields["dnssec_signed"] is True


async def test_handles_partial_record_view() -> None:
    """A domain with only NS + A records still produces a Trace."""
    resolver = _FakeResolver(
        {
            ("example.org", "NS"): ["ns1.example.org."],
            ("example.org", "A"): ["198.51.100.1"],
        }
    )
    collector = DNSCollector(resolver=resolver)
    traces = await collector.collect(_identifier("example.org"))

    assert len(traces) == 1
    fields = traces[0].fields
    assert fields["ns_records"] == ["ns1.example.org."]
    assert fields["a_records"] == ["198.51.100.1"]
    assert fields["mx_records"] == []
    assert fields["mx_hosts"] == []
    assert fields["spf_record"] is None
    assert fields["dmarc_record"] is None
    assert fields["dnssec_signed"] is False


async def test_swallows_per_rtype_errors() -> None:
    """Per-rtype NXDOMAIN / Timeout / NoNameservers do not abort the run."""
    resolver = _FakeResolver(
        {
            ("flaky.test", "NS"): ["ns.flaky.test."],
            ("flaky.test", "MX"): dns.exception.Timeout,
            ("flaky.test", "TXT"): dns.resolver.NoNameservers,
            ("flaky.test", "A"): dns.resolver.NXDOMAIN,
        }
    )
    collector = DNSCollector(resolver=resolver)
    traces = await collector.collect(_identifier("flaky.test"))

    assert len(traces) == 1
    fields = traces[0].fields
    assert fields["ns_records"] == ["ns.flaky.test."]
    assert fields["mx_records"] == []
    assert fields["txt_records"] == []
    assert fields["a_records"] == []


async def test_lowercases_and_strips_input_domain() -> None:
    resolver = _FakeResolver(
        {
            ("example.com", "NS"): ["ns1.example.com."],
        }
    )
    collector = DNSCollector(resolver=resolver)
    traces = await collector.collect(_identifier("  Example.COM.  "))

    assert len(traces) == 1
    assert traces[0].fields["domain"] == "example.com"
    # Verify the resolver was queried with the canonical form.
    assert ("example.com", "NS") in resolver.calls


async def test_extracts_dmarc_only_when_well_formed() -> None:
    """A ``_dmarc.<d>`` TXT that is not a DMARC record is ignored."""
    resolver = _FakeResolver(
        {
            ("example.com", "NS"): ["ns1.example.com."],
            ("_dmarc.example.com", "TXT"): ['"some-other=value"'],
        }
    )
    collector = DNSCollector(resolver=resolver)
    traces = await collector.collect(_identifier())
    assert traces[0].fields["dmarc_record"] is None


async def test_evidence_hash_is_deterministic() -> None:
    """Same input -> same SHA-256, regardless of how many times we run."""
    records: dict[tuple[str, str], list[str] | type[BaseException]] = {
        ("example.com", "NS"): ["ns1.example.com.", "ns2.example.com."],
    }
    a = await DNSCollector(resolver=_FakeResolver(records)).collect(_identifier())
    b = await DNSCollector(resolver=_FakeResolver(records)).collect(_identifier())
    assert a[0].evidence.payload_sha256 == b[0].evidence.payload_sha256


@pytest.mark.parametrize("value", ["", "   ", " . "])
async def test_collect_returns_empty_for_blank_domain(value: str) -> None:
    resolver = _FakeResolver({})
    collector = DNSCollector(resolver=resolver)
    assert await collector.collect(_identifier(value)) == []
