"""Tests for :mod:`reckora.agent.loop`.

These tests run the full propose -> verify -> collect -> gate loop end
to end with **fakes** for the network-touching pieces — the reasoning
client never actually calls OpenAI, the collectors return canned
traces. The point is to nail the control flow: what gets retained,
what gets dropped, and at which gate.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

import pytest

from reckora.agent.loop import AgentLoop
from reckora.agent.verifier import ProposedIdentifier
from reckora.collectors.base import Collector
from reckora.evidence.chain import make_evidence
from reckora.models.entity import Identifier, Trace
from reckora.models.enums import IdentifierType, TraceSource
from reckora.orchestrator import Orchestrator


class _ScriptedReasoningClient:
    """Minimal stand-in for :class:`ReasoningClient`.

    Returns the next scripted response for every ``complete`` call so
    the AgentLoop sees deterministic plans without ever touching the
    network. Records each prompt for assertions.
    """

    def __init__(self, responses: Iterable[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self._responses:
            return ""
        return self._responses.pop(0)


class _FixtureCollector(Collector):
    """Collector that emits a hard-coded trace for matching identifiers."""

    name: ClassVar[str] = "fixture"
    supported: ClassVar[frozenset[str]] = frozenset(
        {
            IdentifierType.USERNAME.value,
            IdentifierType.DOMAIN.value,
            IdentifierType.URL.value,
            IdentifierType.EMAIL.value,
        }
    )

    def __init__(self, traces: dict[Identifier, list[Trace]]) -> None:
        super().__init__()
        self._traces = traces

    async def collect(self, identifier: Identifier) -> list[Trace]:
        return list(self._traces.get(identifier, []))


def _trace(
    identifier: Identifier,
    payload: dict[str, object],
    *,
    source: TraceSource = TraceSource.GITHUB_API,
) -> Trace:
    return Trace(
        identifier=identifier,
        source=source,
        fields=dict(payload),
        evidence=make_evidence(f"https://example.com/{identifier.value}", payload),
    )


@pytest.mark.asyncio
async def test_agent_loop_terminates_when_first_plan_is_empty() -> None:
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    seed_trace = _trace(seed, {"bio": "security researcher"})

    collector = _FixtureCollector({seed: [seed_trace]})
    orchestrator = Orchestrator([collector])
    client = _ScriptedReasoningClient(['{"plan": []}'])

    loop = AgentLoop(orchestrator, client, max_iterations=3)  # type: ignore[arg-type]
    result = await loop.run(seed)

    # No transcript step is recorded for an empty / unparseable plan
    # — we treat it as "stop" rather than "iterate, drop nothing".
    assert result.transcript == ()
    assert seed in result.subject.identifiers
    assert len(result.traces) == 1


@pytest.mark.asyncio
async def test_agent_loop_runs_one_round_when_plan_is_evidenced() -> None:
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    domain = Identifier(type=IdentifierType.DOMAIN, value="alice.dev")

    seed_trace = _trace(seed, {"bio": "alice.dev hacker", "homepage": "alice.dev"})
    # Domain trace shares "alice.dev hacker" bio so the bio-similarity
    # rule fires above its 0.5 threshold and the confidence-floor gate
    # accepts the new identifier.
    domain_trace = _trace(
        domain,
        {"bio": "alice.dev hacker"},
        source=TraceSource.WEB_PROFILE,
    )
    seed_sha8 = seed_trace.evidence.payload_sha256[:8]

    collector = _FixtureCollector({seed: [seed_trace], domain: [domain_trace]})
    orchestrator = Orchestrator([collector])
    plan = (
        '{"plan": [{"kind": "domain", "value": "alice.dev",'
        f' "rationale": "homepage", "evidence_refs": ["{seed_sha8}"]}}]}}'
    )
    client = _ScriptedReasoningClient([plan, '{"plan": []}'])

    loop = AgentLoop(
        orchestrator,
        client,  # type: ignore[arg-type]
        max_iterations=2,
        confidence_floor=0.4,
    )
    result = await loop.run(seed)

    assert len(result.transcript) == 1
    step = result.transcript[0]
    assert step.iteration == 1
    assert step.accepted == (domain,)
    assert step.retained == (domain,)
    assert step.confidence_dropped == ()
    # The orchestrator's collector returned the domain trace and we
    # kept it around for the final result.
    assert any(t.identifier == domain for t in result.traces)
    assert domain in result.subject.identifiers


@pytest.mark.asyncio
async def test_agent_loop_drops_proposals_below_confidence_floor() -> None:
    """Verifier accepts but the rule-based engine cannot link the proposal back.

    The proposed domain shares no evidence-justifying signal with the
    seed (different bio, no shared identifier substring, no avatar),
    so the bio-similarity rule emits no edge and the confidence-floor
    gate drops the candidate.
    """
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    domain = Identifier(type=IdentifierType.DOMAIN, value="alice.dev")

    seed_trace = _trace(seed, {"bio": "completely unrelated text"})
    # The collector returns a trace that intentionally shares zero
    # signal with the seed trace.
    domain_trace = _trace(
        domain,
        {"bio": "totally different domain content"},
        source=TraceSource.WEB_PROFILE,
    )
    seed_sha8 = seed_trace.evidence.payload_sha256[:8]

    collector = _FixtureCollector({seed: [seed_trace], domain: [domain_trace]})
    orchestrator = Orchestrator([collector])
    plan = (
        '{"plan": [{"kind": "domain", "value": "alice.dev",'
        f' "rationale": "guess", "evidence_refs": ["{seed_sha8}"]}}]}}'
    )
    client = _ScriptedReasoningClient([plan])

    loop = AgentLoop(
        orchestrator,
        client,  # type: ignore[arg-type]
        max_iterations=2,
        confidence_floor=0.5,
    )
    result = await loop.run(seed)

    assert len(result.transcript) == 1
    step = result.transcript[0]
    assert step.accepted == (domain,)
    assert step.retained == ()
    assert step.confidence_dropped == (domain,)
    # The dropped trace should not appear in the final result.
    assert all(t.identifier != domain for t in result.traces)
    assert domain not in result.subject.identifiers


@pytest.mark.asyncio
async def test_agent_loop_records_verifier_rejections_on_transcript() -> None:
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    seed_trace = _trace(seed, {"bio": "x"})

    collector = _FixtureCollector({seed: [seed_trace]})
    orchestrator = Orchestrator([collector])
    plan = (
        '{"plan": ['
        '{"kind": "not-a-kind", "value": "junk", "evidence_refs": ["abcdef12"]},'
        '{"kind": "domain", "value": "alice.dev", "evidence_refs": ["ffffffff"]}'
        "]}"
    )
    client = _ScriptedReasoningClient([plan])

    loop = AgentLoop(orchestrator, client, max_iterations=1)  # type: ignore[arg-type]
    result = await loop.run(seed)

    assert len(result.transcript) == 1
    step = result.transcript[0]
    # Both proposals should land in `rejected`; nothing should be
    # accepted, retained, or collected.
    assert step.accepted == ()
    assert step.retained == ()
    assert len(step.rejected) == 2
    assert step.new_traces == ()
    assert {r.proposal.value for r in step.rejected} == {"junk", "alice.dev"}


@pytest.mark.asyncio
async def test_agent_loop_caps_proposals_per_iteration() -> None:
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    seed_trace = _trace(seed, {"bio": "hacker"})
    seed_sha8 = seed_trace.evidence.payload_sha256[:8]

    collector = _FixtureCollector({seed: [seed_trace]})
    orchestrator = Orchestrator([collector])

    # Six well-formed proposals; the loop must cap at three.
    proposals_json = ",".join(
        f'{{"kind": "domain", "value": "alice{i}.dev",'
        f' "rationale": "x", "evidence_refs": ["{seed_sha8}"]}}'
        for i in range(6)
    )
    plan = f'{{"plan": [{proposals_json}]}}'
    client = _ScriptedReasoningClient([plan, '{"plan": []}'])

    loop = AgentLoop(
        orchestrator,
        client,  # type: ignore[arg-type]
        max_iterations=2,
        max_proposals_per_iteration=3,
        confidence_floor=0.0,
    )
    result = await loop.run(seed)

    assert len(result.transcript) == 1
    step = result.transcript[0]
    assert len(step.proposals) == 3


@pytest.mark.asyncio
async def test_agent_loop_terminates_after_failed_round() -> None:
    """A round with zero retained candidates ends the loop immediately.

    Even though ``max_iterations=3`` is set, the second iteration is
    never invoked because the first produced no retained identifiers.
    """
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    seed_trace = _trace(seed, {"bio": "x"})

    collector = _FixtureCollector({seed: [seed_trace]})
    orchestrator = Orchestrator([collector])
    plan = (
        '{"plan": [{"kind": "domain", "value": "alice.dev",'
        ' "rationale": "no-evidence", "evidence_refs": []}]}'
    )
    client = _ScriptedReasoningClient([plan, '{"plan": []}', '{"plan": []}'])

    loop = AgentLoop(orchestrator, client, max_iterations=3)  # type: ignore[arg-type]
    await loop.run(seed)

    # The reasoning client was only consulted once because the loop
    # stopped as soon as iteration 1 returned no retained candidates.
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_agent_loop_handles_unparseable_plan() -> None:
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    seed_trace = _trace(seed, {"bio": "x"})

    collector = _FixtureCollector({seed: [seed_trace]})
    orchestrator = Orchestrator([collector])
    client = _ScriptedReasoningClient(["totally not json", '{"plan": []}'])

    loop = AgentLoop(orchestrator, client, max_iterations=2)  # type: ignore[arg-type]
    result = await loop.run(seed)

    assert result.transcript == ()
    # The seed trace still made it into the final state.
    assert seed in result.subject.identifiers


@pytest.mark.asyncio
async def test_agent_loop_propagates_visited_to_block_re_proposal() -> None:
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    domain = Identifier(type=IdentifierType.DOMAIN, value="alice.dev")

    seed_trace = _trace(seed, {"bio": "alice.dev hacker"})
    domain_trace = _trace(
        domain,
        {"bio": "alice.dev hacker"},
        source=TraceSource.WEB_PROFILE,
    )
    seed_sha8 = seed_trace.evidence.payload_sha256[:8]

    collector = _FixtureCollector({seed: [seed_trace], domain: [domain_trace]})
    orchestrator = Orchestrator([collector])

    plan_round_1 = (
        '{"plan": [{"kind": "domain", "value": "alice.dev",'
        f' "rationale": "x", "evidence_refs": ["{seed_sha8}"]}}]}}'
    )
    plan_round_2 = (
        '{"plan": [{"kind": "domain", "value": "alice.dev",'
        f' "rationale": "still here", "evidence_refs": ["{seed_sha8}"]}}]}}'
    )
    client = _ScriptedReasoningClient([plan_round_1, plan_round_2])

    loop = AgentLoop(
        orchestrator,
        client,  # type: ignore[arg-type]
        max_iterations=2,
        confidence_floor=0.4,
    )
    result = await loop.run(seed)

    # Both iterations ran; the second one's proposal must be rejected
    # because the verifier sees ``alice.dev`` in the ``visited`` set.
    assert len(result.transcript) == 2
    step_two = result.transcript[1]
    assert step_two.accepted == ()
    assert any("already investigated" in r.reason for r in step_two.rejected)


def test_proposed_identifier_to_identifier_round_trips() -> None:
    proposal = ProposedIdentifier(kind="username", value="alice", evidence_refs=("abcdef12",))
    ident = proposal.to_identifier()
    assert ident.type == IdentifierType.USERNAME
    assert ident.value == "alice"


def test_agent_loop_constructor_validates_arguments() -> None:
    seed_trace = _trace(Identifier(type=IdentifierType.USERNAME, value="x"), {"bio": "y"})
    collector = _FixtureCollector({seed_trace.identifier: [seed_trace]})
    orchestrator = Orchestrator([collector])
    client = _ScriptedReasoningClient([])

    with pytest.raises(ValueError):
        AgentLoop(orchestrator, client, max_iterations=0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AgentLoop(orchestrator, client, confidence_floor=-0.1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AgentLoop(orchestrator, client, confidence_floor=1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AgentLoop(orchestrator, client, max_proposals_per_iteration=0)  # type: ignore[arg-type]
