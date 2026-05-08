"""Shared pytest fixtures."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reckora.collectors.base import Collector
from reckora.evidence.chain import make_evidence
from reckora.models.entity import Identifier, Trace
from reckora.models.enums import IdentifierType, TraceSource
from reckora.orchestrator import Orchestrator
from reckora_api.config import APISettings
from reckora_api.main import create_app


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


# --- API fixtures ---------------------------------------------------------


class _FakeCollector(Collector):
    """Deterministic collector used by API tests so we never hit the network."""

    name = "fake"
    supported = frozenset({"username", "domain"})

    async def collect(self, identifier: Identifier) -> list[Trace]:
        evidence = make_evidence(
            f"https://fake.example.com/{identifier.value}",
            {"login": identifier.value, "kind": identifier.type.value},
        )
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.WEB_PROFILE,
                fields={"platform": "fake", "display_name": identifier.value},
                evidence=evidence,
            )
        ]


@pytest.fixture
def api_settings(tmp_path: Path) -> APISettings:
    """An ``APISettings`` instance with a fresh DB and a random JWT secret."""
    return APISettings(
        jwt_secret=secrets.token_urlsafe(32),
        jwt_ttl_seconds=3600,
        db_path=str(tmp_path / "reckora.db"),
        cors_origins_raw="http://localhost:5173",
        docs_enabled=True,
    )


@pytest.fixture
def client(api_settings: APISettings) -> Iterator[TestClient]:
    """A ``TestClient`` wired to a fake (deterministic) orchestrator."""
    app = create_app(
        api_settings,
        orchestrator_factory=lambda: Orchestrator([_FakeCollector()]),
    )
    with TestClient(app) as c:
        yield c


@pytest.fixture
def authed_client(client: TestClient) -> TestClient:
    """A client carrying a valid bearer token for the user ``alice``."""
    client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "supersecret123"},
    )
    response = client.post(
        "/api/v1/auth/token",
        data={"username": "alice", "password": "supersecret123"},
    )
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client
