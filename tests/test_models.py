"""Tests for the entity model."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from reckora.models.entity import Edge, Evidence, Identifier, Subject, Trace
from reckora.models.enums import EdgeKind, IdentifierType, TraceSource


def test_identifier_is_frozen_and_hashable() -> None:
    a = Identifier(type=IdentifierType.USERNAME, value="alice")
    b = Identifier(type=IdentifierType.USERNAME, value="alice")
    assert a == b
    assert hash(a) == hash(b)
    with pytest.raises(ValidationError):
        a.value = "bob"


def test_identifier_str() -> None:
    ident = Identifier(type=IdentifierType.DOMAIN, value="example.com")
    assert str(ident) == "domain:example.com"


def test_evidence_is_frozen() -> None:
    ev = Evidence(
        source_url="https://example.com",
        fetched_at=datetime.now(UTC),
        payload_sha256="0" * 64,
    )
    with pytest.raises(ValidationError):
        ev.source_url = "x"


def test_edge_confidence_bounds() -> None:
    src = Identifier(type=IdentifierType.USERNAME, value="a")
    dst = Identifier(type=IdentifierType.USERNAME, value="b")
    Edge(
        source=src,
        target=dst,
        kind=EdgeKind.USERNAME_MUTATION,
        confidence=0.5,
        reasons=["test"],
    )
    with pytest.raises(ValidationError):
        Edge(
            source=src,
            target=dst,
            kind=EdgeKind.USERNAME_MUTATION,
            confidence=1.5,
            reasons=["test"],
        )
    with pytest.raises(ValidationError):
        Edge(
            source=src,
            target=dst,
            kind=EdgeKind.USERNAME_MUTATION,
            confidence=-0.1,
            reasons=["test"],
        )


def test_subject_defaults() -> None:
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    s = Subject(id="subj-1", seed_identifier=seed)
    assert s.identifiers == []
    assert s.traces == []


def test_trace_carries_evidence(github_trace_alice: Trace) -> None:
    assert github_trace_alice.source == TraceSource.GITHUB_API
    assert github_trace_alice.evidence.source_url.startswith("https://")
    assert len(github_trace_alice.evidence.payload_sha256) == 64
