"""Tests for the offline ``phone`` collector backed by ``libphonenumber``.

These tests exercise real ``phonenumbers`` parsing — the library ships its
own metadata bundle and never goes to the network, so the behaviour is
deterministic and there is no I/O to mock.
"""

from __future__ import annotations

import pytest

from reckora.collectors.phone import PHONE_SOURCE_URL_PREFIX, PhoneCollector
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType, TraceSource


@pytest.fixture
def collector() -> PhoneCollector:
    return PhoneCollector()


async def test_collect_skips_unsupported_identifier(collector: PhoneCollector) -> None:
    ident = Identifier(type=IdentifierType.USERNAME, value="alice")
    assert await collector.collect(ident) == []


async def test_collect_invalid_string_returns_empty(collector: PhoneCollector) -> None:
    ident = Identifier(type=IdentifierType.PHONE, value="not-a-phone-number")
    assert await collector.collect(ident) == []


async def test_collect_e164_us_number_normalises_to_canonical_form(
    collector: PhoneCollector,
) -> None:
    ident = Identifier(type=IdentifierType.PHONE, value="+12025550123")
    traces = await collector.collect(ident)

    assert len(traces) == 1
    trace = traces[0]
    assert trace.source == TraceSource.PHONE_LIBPHONENUMBER
    assert trace.identifier == ident

    fields = trace.fields
    assert fields["e164"] == "+12025550123"
    assert fields["country_code"] == 1
    assert fields["country_iso"] == "US"
    assert fields["country_name"] == "United States"
    # Sub-national geocode for area code 202 is Washington D.C., distinct
    # from the country name so the dedupe path leaves it populated.
    assert fields["region"] == "Washington D.C."
    assert fields["is_valid"] is True
    assert fields["is_possible"] is True
    assert fields["line_type"] in {
        "fixed_line",
        "mobile",
        "fixed_line_or_mobile",
        "unknown",
    }


async def test_collect_local_indonesian_number_with_default_region() -> None:
    """``08...`` is unambiguous only when ``default_region='ID'``."""
    collector = PhoneCollector(default_region="ID")
    ident = Identifier(type=IdentifierType.PHONE, value="08123456789")
    traces = await collector.collect(ident)

    assert len(traces) == 1
    fields = traces[0].fields
    assert fields["e164"] == "+628123456789"
    assert fields["country_code"] == 62
    assert fields["country_iso"] == "ID"
    assert fields["country_name"] == "Indonesia"
    # libphonenumber's geocoder returns "Indonesia" for the description
    # too when there is no finer sub-national signal — the collector
    # de-duplicates that to None so dossiers don't show "Indonesia /
    # Indonesia".
    assert fields["region"] is None
    assert fields["is_valid"] is True
    assert fields["line_type"] == "mobile"


async def test_collect_default_region_us_rejects_local_id_number() -> None:
    """Without the right ``default_region`` a national string is invalid."""
    collector = PhoneCollector(default_region="US")
    ident = Identifier(type=IdentifierType.PHONE, value="08123456789")
    traces = await collector.collect(ident)

    # Either parses to something invalid (returned with is_valid=False) or
    # fails to parse at all (empty list). Both are acceptable; what matters
    # is that we never claim it's a valid US number.
    if traces:
        assert traces[0].fields["is_valid"] is False
    else:
        assert traces == []


async def test_collect_attaches_evidence_with_synthetic_source_url(
    collector: PhoneCollector,
) -> None:
    ident = Identifier(type=IdentifierType.PHONE, value="+12025550123")
    traces = await collector.collect(ident)

    assert len(traces) == 1
    evidence = traces[0].evidence
    assert evidence.source_url == f"{PHONE_SOURCE_URL_PREFIX}+12025550123"
    # Hash should be a 64-char lowercase hex string from sha256 of the
    # canonicalised payload.
    assert len(evidence.payload_sha256) == 64
    assert all(c in "0123456789abcdef" for c in evidence.payload_sha256)


async def test_supports_only_phone_identifier(collector: PhoneCollector) -> None:
    assert collector.supports(Identifier(type=IdentifierType.PHONE, value="+12025550123"))
    assert not collector.supports(Identifier(type=IdentifierType.EMAIL, value="a@b.co"))
    assert not collector.supports(Identifier(type=IdentifierType.DOMAIN, value="a.co"))


async def test_collector_is_offline_no_http_client_constructed(
    collector: PhoneCollector,
) -> None:
    """Sanity: the constructor never touches an httpx client.

    This protects future maintainers who might be tempted to bolt on an
    online enrichment step — if you do, please bump the
    :class:`TraceSource` enum so dossiers can distinguish offline parsing
    from online lookups.
    """
    # ``Collector._client`` is the only place an httpx client could live;
    # for the phone collector it must always be None.
    assert collector._client is None
