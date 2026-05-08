"""Shared pytest fixtures."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from reckora.evidence.chain import make_evidence
from reckora.models.entity import Identifier, Trace
from reckora.models.enums import IdentifierType, TraceSource


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def github_trace_alice(fixed_now: datetime) -> Trace:
    ident = Identifier(type=IdentifierType.USERNAME, value="alice")
    payload = {
        "login": "alice",
        "name": "Alice A",
        "bio": "Security researcher and OSINT enthusiast.",
        "avatar_url": "https://example.com/alice.png",
    }
    evidence = make_evidence(
        "https://api.github.com/users/alice",
        payload,
        keep_raw=False,
        fetched_at=fixed_now,
    )
    return Trace(
        identifier=ident,
        source=TraceSource.GITHUB_API,
        fields={
            "platform": "github",
            "display_name": "Alice A",
            "bio": "Security researcher and OSINT enthusiast.",
            "avatar_phash": "ffeeddccbbaa9988",
            "activity_hours_utc": [9, 10, 11, 12, 13, 14, 15, 16],
        },
        evidence=evidence,
    )


@pytest.fixture
def web_trace_alice_twin(fixed_now: datetime) -> Trace:
    """A second trace from a different identifier that we expect to correlate
    back to the github trace via avatar / bio / timezone signals.
    """
    payload = {"status": 200, "og": {"title": "alice"}, "title": "alice"}
    evidence = make_evidence(
        "https://example.org/@alice",
        payload,
        fetched_at=fixed_now,
    )
    return Trace(
        identifier=Identifier(type=IdentifierType.URL, value="https://example.org/@alice"),
        source=TraceSource.WEB_PROFILE,
        fields={
            "platform": "example.org",
            "display_name": "alice",
            "bio": "Security researcher, OSINT and incident response.",
            "avatar_phash": "ffeeddccbbaa9989",
            "activity_hours_utc": [10, 11, 12, 13, 14, 15],
        },
        evidence=evidence,
    )
