"""Tests for the orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime

from reckora.collectors.base import Collector
from reckora.evidence.chain import make_evidence
from reckora.models.entity import Identifier, Trace
from reckora.models.enums import IdentifierType, TraceSource
from reckora.orchestrator import Orchestrator


class _FakeCollector(Collector):
    name = "fake"
    supported = frozenset({"username"})

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[Identifier] = []

    async def collect(self, identifier: Identifier) -> list[Trace]:
        self.calls.append(identifier)
        evidence = make_evidence(
            f"https://fake/{identifier.value}",
            {"login": identifier.value},
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.WEB_PROFILE,
                fields={"platform": "fake"},
                evidence=evidence,
            )
        ]


class _BrokenCollector(Collector):
    name = "broken"
    supported = frozenset({"username"})

    async def collect(self, identifier: Identifier) -> list[Trace]:
        raise RuntimeError("boom")


async def test_orchestrator_collects_and_packages_subject() -> None:
    fake = _FakeCollector()
    orchestrator = Orchestrator([fake])
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    extras = [Identifier(type=IdentifierType.USERNAME, value="bob")]

    subject, traces, edges = await orchestrator.investigate(seed, extra_identifiers=extras)

    assert subject.seed_identifier == seed
    assert subject.identifiers == [seed, *extras]
    assert len(traces) == 2
    assert {t.identifier.value for t in traces} == {"alice", "bob"}
    assert subject.id.startswith("subj-")
    # Two distinct usernames -> at least one username_mutation edge possible
    # but we don't assert it (rule may not fire); just ensure no crash.
    for e in edges:
        assert 0.0 < e.confidence <= 1.0


async def test_orchestrator_swallows_collector_exceptions() -> None:
    fake = _FakeCollector()
    broken = _BrokenCollector()
    orchestrator = Orchestrator([broken, fake])
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    subject, traces, _edges = await orchestrator.investigate(seed)
    assert len(traces) == 1
    assert traces[0].identifier == seed
    assert subject.seed_identifier == seed


async def test_orchestrator_skips_unsupported_identifiers() -> None:
    fake = _FakeCollector()
    orchestrator = Orchestrator([fake])
    seed = Identifier(type=IdentifierType.DOMAIN, value="example.com")
    subject, traces, _edges = await orchestrator.investigate(seed)
    assert traces == []
    assert subject.seed_identifier == seed
    assert fake.calls == []
