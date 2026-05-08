"""Tests for the rule-based anomaly detector."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from reckora.anomaly import Anomaly, AnomalyKind, AnomalySeverity, detect_anomalies
from reckora.anomaly.rules import (
    domain_expiry,
    name_divergence,
    phone_validity,
    temporal,
)
from reckora.anomaly.rules.temporal import _parse_iso
from reckora.evidence.chain import make_evidence
from reckora.models.entity import Identifier, Trace
from reckora.models.enums import IdentifierType, TraceSource

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _trace(
    *,
    source: TraceSource = TraceSource.GITHUB_API,
    identifier: Identifier | None = None,
    fields: dict[str, Any] | None = None,
    fetched_at: datetime = NOW,
    source_url: str = "https://example.com/x",
) -> Trace:
    """Build a Trace with deterministic evidence for the rule tests."""
    ident = identifier or Identifier(type=IdentifierType.USERNAME, value="alice")
    payload = fields if fields is not None else {"login": ident.value}
    return Trace(
        identifier=ident,
        source=source,
        fields=fields if fields is not None else dict(payload),
        evidence=make_evidence(source_url, payload, fetched_at=fetched_at),
    )


def test_anomaly_model_is_frozen() -> None:
    a = Anomaly(
        kind=AnomalyKind.FUTURE_EVIDENCE,
        severity=AnomalySeverity.HIGH,
        message="x",
        supporting_evidence=["abc"],
    )
    with pytest.raises(ValidationError):
        a.message = "y"


def test_parse_iso_handles_z_suffix_and_naive_strings() -> None:
    assert _parse_iso("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=UTC)
    # Naive strings get tagged with UTC.
    assert _parse_iso("2026-01-01T00:00:00") == datetime(2026, 1, 1, tzinfo=UTC)
    assert _parse_iso("2026-01-01T00:00:00+05:00") == datetime(
        2026,
        1,
        1,
        tzinfo=_parse_iso("2026-01-01T00:00:00+05:00").tzinfo,  # type: ignore[union-attr]
    )
    assert _parse_iso(None) is None
    assert _parse_iso("") is None
    assert _parse_iso("definitely-not-a-date") is None
    assert _parse_iso(12345) is None


def test_temporal_rule_flags_future_evidence() -> None:
    future = _trace(fetched_at=NOW + timedelta(hours=2))
    findings = temporal.detect([future], now=NOW)
    assert len(findings) == 1
    f = findings[0]
    assert f.kind is AnomalyKind.FUTURE_EVIDENCE
    assert f.severity is AnomalySeverity.HIGH
    assert future.evidence.payload_sha256 in f.supporting_evidence
    assert "future" in f.message.lower()


def test_temporal_rule_flags_created_after_updated() -> None:
    trace = _trace(
        fields={"created_at": "2026-02-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"},
    )
    findings = temporal.detect([trace], now=NOW)
    kinds = {f.kind for f in findings}
    assert AnomalyKind.TEMPORAL_INCONSISTENCY in kinds


def test_temporal_rule_flags_created_after_observed() -> None:
    """``created_at`` postdating ``Evidence.fetched_at`` is impossible."""
    trace = _trace(
        fields={"created_at": "2099-01-01T00:00:00Z"},
        fetched_at=NOW,
    )
    findings = temporal.detect([trace], now=NOW)
    assert any(f.kind is AnomalyKind.TEMPORAL_INCONSISTENCY for f in findings)


def test_temporal_rule_quiet_on_consistent_data() -> None:
    trace = _trace(
        fields={"created_at": "2020-01-01T00:00:00Z", "updated_at": "2025-06-01T00:00:00Z"},
        fetched_at=NOW,
    )
    assert temporal.detect([trace], now=NOW) == []


def test_temporal_rule_ignores_unparseable_timestamps() -> None:
    trace = _trace(fields={"created_at": "yesterday", "updated_at": 12345})
    assert temporal.detect([trace], now=NOW) == []


def test_domain_expiry_rule_flags_lapsed_domain() -> None:
    domain = Identifier(type=IdentifierType.DOMAIN, value="example.org")
    trace = _trace(
        identifier=domain,
        source=TraceSource.WHOIS_RDAP,
        fields={"domain": "example.org", "expires_at": "2025-01-01T00:00:00Z"},
        fetched_at=NOW,
        source_url="https://rdap.org/domain/example.org",
    )
    findings = domain_expiry.detect([trace], now=NOW)
    assert len(findings) == 1
    f = findings[0]
    assert f.kind is AnomalyKind.EXPIRED_DOMAIN
    assert f.severity is AnomalySeverity.MEDIUM
    assert "example.org" in f.message
    assert "365 day" in f.message


def test_domain_expiry_rule_quiet_on_active_domain() -> None:
    domain = Identifier(type=IdentifierType.DOMAIN, value="example.org")
    trace = _trace(
        identifier=domain,
        source=TraceSource.WHOIS_RDAP,
        fields={"domain": "example.org", "expires_at": "2030-01-01T00:00:00Z"},
        fetched_at=NOW,
    )
    assert domain_expiry.detect([trace], now=NOW) == []


def test_domain_expiry_rule_skips_non_whois_traces() -> None:
    trace = _trace(
        source=TraceSource.GITHUB_API,
        fields={"expires_at": "2000-01-01T00:00:00Z"},
        fetched_at=NOW,
    )
    assert domain_expiry.detect([trace], now=NOW) == []


def test_domain_expiry_rule_falls_back_to_identifier_value() -> None:
    domain = Identifier(type=IdentifierType.DOMAIN, value="example.org")
    trace = _trace(
        identifier=domain,
        source=TraceSource.WHOIS_RDAP,
        fields={"expires_at": "2025-01-01T00:00:00Z"},  # no "domain" field
        fetched_at=NOW,
    )
    findings = domain_expiry.detect([trace], now=NOW)
    assert findings
    assert "example.org" in findings[0].message


def test_phone_validity_rule_flags_invalid_phone() -> None:
    ident = Identifier(type=IdentifierType.PHONE, value="+11111111111")
    trace = _trace(
        identifier=ident,
        source=TraceSource.PHONE_LIBPHONENUMBER,
        fields={"e164": "+11111111111", "is_valid": False, "is_possible": True},
    )
    findings = phone_validity.detect([trace], now=NOW)
    assert len(findings) == 1
    f = findings[0]
    assert f.kind is AnomalyKind.INVALID_PHONE
    assert f.severity is AnomalySeverity.MEDIUM
    assert "+11111111111" in f.message
    assert "syntactically possible" in f.message


def test_phone_validity_rule_message_when_not_possible() -> None:
    ident = Identifier(type=IdentifierType.PHONE, value="garbage")
    trace = _trace(
        identifier=ident,
        source=TraceSource.PHONE_LIBPHONENUMBER,
        fields={"e164": None, "is_valid": False, "is_possible": False},
    )
    findings = phone_validity.detect([trace], now=NOW)
    assert findings
    assert "neither valid nor syntactically possible" in findings[0].message


def test_phone_validity_rule_quiet_on_valid_phone() -> None:
    trace = _trace(
        source=TraceSource.PHONE_LIBPHONENUMBER,
        fields={"e164": "+1650...", "is_valid": True},
    )
    assert phone_validity.detect([trace], now=NOW) == []


def test_phone_validity_rule_skips_non_phone_sources() -> None:
    trace = _trace(source=TraceSource.GITHUB_API, fields={"is_valid": False})
    assert phone_validity.detect([trace], now=NOW) == []


def test_name_divergence_rule_flags_multiple_distinct_names() -> None:
    trace_a = _trace(
        source=TraceSource.GITHUB_API,
        fields={"display_name": "Alice Wonder"},
        source_url="https://example.com/a",
    )
    trace_b = _trace(
        source=TraceSource.WEB_PROFILE,
        identifier=Identifier(type=IdentifierType.URL, value="https://example.org/@alice"),
        fields={"display_name": "Bob Stranger"},
        source_url="https://example.com/b",
    )
    findings = name_divergence.detect([trace_a, trace_b], now=NOW)
    assert len(findings) == 1
    f = findings[0]
    assert f.kind is AnomalyKind.NAME_DIVERGENCE
    assert f.severity is AnomalySeverity.LOW
    assert "Alice Wonder" in f.message
    assert "Bob Stranger" in f.message
    assert trace_a.evidence.payload_sha256 in f.supporting_evidence
    assert trace_b.evidence.payload_sha256 in f.supporting_evidence


def test_name_divergence_rule_canonicalises_case_and_whitespace() -> None:
    trace_a = _trace(fields={"display_name": "Alice Wonder"}, source_url="https://example.com/a")
    trace_b = _trace(
        fields={"display_name": "  alice   wonder "}, source_url="https://example.com/b"
    )
    # Same canonical form -> no divergence.
    assert name_divergence.detect([trace_a, trace_b], now=NOW) == []


def test_name_divergence_rule_skips_blank_or_missing_names() -> None:
    trace_a = _trace(fields={"display_name": "Alice"}, source_url="https://example.com/a")
    trace_b = _trace(fields={"display_name": ""}, source_url="https://example.com/b")
    trace_c = _trace(fields={"display_name": None}, source_url="https://example.com/c")
    trace_d = _trace(fields={}, source_url="https://example.com/d")
    assert name_divergence.detect([trace_a, trace_b, trace_c, trace_d], now=NOW) == []


def test_detect_anomalies_returns_empty_for_clean_traces() -> None:
    trace = _trace(
        fields={"display_name": "Alice", "created_at": "2020-01-01T00:00:00Z"},
        fetched_at=NOW,
    )
    assert detect_anomalies([trace], now=NOW) == []


def test_detect_anomalies_sorts_high_severity_first() -> None:
    domain = Identifier(type=IdentifierType.DOMAIN, value="example.org")
    expired = _trace(
        identifier=domain,
        source=TraceSource.WHOIS_RDAP,
        fields={"domain": "example.org", "expires_at": "2025-01-01T00:00:00Z"},
        source_url="https://rdap.org/domain/example.org",
    )
    future = _trace(
        fields={"display_name": "Alice"},
        fetched_at=NOW + timedelta(days=1),
        source_url="https://example.com/future",
    )
    name_a = _trace(fields={"display_name": "Alice"}, source_url="https://example.com/a")
    name_b = _trace(
        fields={"display_name": "Bob"},
        identifier=Identifier(type=IdentifierType.URL, value="https://example.org/b"),
        source_url="https://example.com/b",
    )

    findings = detect_anomalies([expired, future, name_a, name_b], now=NOW)
    severities = [f.severity for f in findings]
    assert severities[0] is AnomalySeverity.HIGH  # FUTURE_EVIDENCE first
    # HIGH severities come before MEDIUM, MEDIUM before LOW.
    rank = {AnomalySeverity.HIGH: 0, AnomalySeverity.MEDIUM: 1, AnomalySeverity.LOW: 2}
    assert severities == sorted(severities, key=lambda s: rank[s])
    kinds = {f.kind for f in findings}
    assert {
        AnomalyKind.FUTURE_EVIDENCE,
        AnomalyKind.EXPIRED_DOMAIN,
        AnomalyKind.NAME_DIVERGENCE,
    }.issubset(kinds)


def test_detect_anomalies_defaults_now_to_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``now`` is omitted the engine still produces deterministic findings.

    The trace's ``fetched_at`` is the engine's reference clock for the
    domain-expiry rule, so this assertion holds regardless of what
    ``datetime.now`` returns.
    """
    domain = Identifier(type=IdentifierType.DOMAIN, value="example.org")
    expired = _trace(
        identifier=domain,
        source=TraceSource.WHOIS_RDAP,
        fields={"domain": "example.org", "expires_at": "2024-01-01T00:00:00Z"},
        fetched_at=datetime(2026, 6, 1, tzinfo=UTC),
        source_url="https://rdap.org/domain/example.org",
    )
    findings = detect_anomalies([expired])
    assert any(f.kind is AnomalyKind.EXPIRED_DOMAIN for f in findings)
