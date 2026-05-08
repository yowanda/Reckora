"""Tests for :mod:`reckora.agent.verifier`.

The verifier is the rule-based gate sitting between the LLM and the
collectors. These tests exercise both the JSON parser (which tolerates
fenced code blocks, alternate key spellings, and outright garbage) and
the rule pipeline (which rejects malformed, unsupported, or
unevidenced proposals).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from reckora.agent.verifier import (
    ProposedIdentifier,
    Verifier,
    parse_plan,
)
from reckora.collectors.base import Collector
from reckora.evidence.chain import make_evidence
from reckora.models.entity import Identifier, Trace
from reckora.models.enums import IdentifierType, TraceSource


class _FakeCollector(Collector):
    name: ClassVar[str] = "fake"
    supported: ClassVar[frozenset[str]]

    def __init__(self, supported: Iterable[str]) -> None:
        super().__init__()
        # ``supported`` is a ClassVar in the protocol, but the agent
        # only needs an instance-level ``supports()`` to work.
        type(self).supported = frozenset(supported)

    async def collect(self, identifier: Identifier) -> list[Trace]:
        return []


def _trace(identifier: Identifier, payload: dict[str, object]) -> Trace:
    return Trace(
        identifier=identifier,
        source=TraceSource.GITHUB_API,
        fields=dict(payload),
        evidence=make_evidence("https://example.com", payload),
    )


def test_parse_plan_handles_canonical_object() -> None:
    raw = (
        '{"plan": [{"kind": "domain", "value": "alice.dev",'
        ' "rationale": "found in bio", "evidence_refs": ["a1b2c3d4"]}]}'
    )
    proposals = parse_plan(raw)
    assert len(proposals) == 1
    assert proposals[0].kind == "domain"
    assert proposals[0].value == "alice.dev"
    assert proposals[0].evidence_refs == ("a1b2c3d4",)


def test_parse_plan_strips_markdown_fence() -> None:
    raw = """```json
    {"plan": [{"kind": "username", "value": "alice", "rationale": "x", "evidence_refs": ["abcdef12"]}]}
    ```"""
    proposals = parse_plan(raw)
    assert len(proposals) == 1
    assert proposals[0].value == "alice"


def test_parse_plan_accepts_top_level_array() -> None:
    raw = '[{"kind": "username", "value": "alice", "rationale": "", "evidence_refs": ["abcdef12"]}]'
    proposals = parse_plan(raw)
    assert len(proposals) == 1


def test_parse_plan_accepts_alternate_key_spellings() -> None:
    raw = (
        '{"proposals": [{"type": "username", "value": "alice",'
        ' "reason": "bio match", "evidence": ["abcdef12"]}]}'
    )
    proposals = parse_plan(raw)
    assert len(proposals) == 1
    assert proposals[0].kind == "username"
    assert proposals[0].rationale == "bio match"
    assert proposals[0].evidence_refs == ("abcdef12",)


def test_parse_plan_returns_empty_on_garbage() -> None:
    assert parse_plan("") == []
    assert parse_plan("not json") == []
    assert parse_plan("{") == []
    assert parse_plan('"just a string"') == []
    assert parse_plan("null") == []


def test_parse_plan_skips_malformed_entries() -> None:
    raw = (
        '{"plan": [{"value": "missing-kind"},'
        '          {"kind": "username", "value": "alice", "evidence_refs": []}]}'
    )
    proposals = parse_plan(raw)
    # The first entry is dropped; the second is parsed even though it
    # carries no evidence (the verifier — not the parser — owns the
    # evidence requirement).
    assert len(proposals) == 1
    assert proposals[0].value == "alice"


def test_verifier_accepts_well_formed_proposal() -> None:
    seed = Identifier(type=IdentifierType.USERNAME, value="seed")
    trace = _trace(seed, {"bio": "alice.dev"})
    sha8 = trace.evidence.payload_sha256[:8]

    collector = _FakeCollector({IdentifierType.DOMAIN.value})
    verifier = Verifier([collector])
    result = verifier.verify(
        [
            ProposedIdentifier(
                kind="domain",
                value="alice.dev",
                rationale="bio mentions alice.dev",
                evidence_refs=(sha8,),
            )
        ],
        existing_traces=[trace],
        visited={seed},
    )
    assert len(result.accepted) == 1
    assert result.accepted[0].type == IdentifierType.DOMAIN
    assert result.rejected == ()


def test_verifier_rejects_unknown_kind() -> None:
    verifier = Verifier([_FakeCollector({IdentifierType.DOMAIN.value})])
    result = verifier.verify(
        [ProposedIdentifier(kind="not-a-kind", value="x", evidence_refs=("abcdef12",))],
        existing_traces=[],
        visited=set(),
    )
    assert result.accepted == ()
    assert len(result.rejected) == 1
    assert "unknown identifier kind" in result.rejected[0].reason


def test_verifier_rejects_empty_value() -> None:
    verifier = Verifier([_FakeCollector({IdentifierType.DOMAIN.value})])
    result = verifier.verify(
        [ProposedIdentifier(kind="domain", value="", evidence_refs=("abcdef12",))],
        existing_traces=[],
        visited=set(),
    )
    assert result.accepted == ()
    assert "empty" in result.rejected[0].reason.lower()


def test_verifier_rejects_unsupported_collector() -> None:
    seed = Identifier(type=IdentifierType.USERNAME, value="seed")
    trace = _trace(seed, {"x": 1})
    sha8 = trace.evidence.payload_sha256[:8]

    # Collector only supports usernames.
    verifier = Verifier([_FakeCollector({IdentifierType.USERNAME.value})])
    result = verifier.verify(
        [
            ProposedIdentifier(
                kind="domain",
                value="alice.dev",
                evidence_refs=(sha8,),
            )
        ],
        existing_traces=[trace],
        visited={seed},
    )
    assert result.accepted == ()
    assert "no collector supports" in result.rejected[0].reason


def test_verifier_rejects_already_visited() -> None:
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    trace = _trace(seed, {"x": 1})
    sha8 = trace.evidence.payload_sha256[:8]

    verifier = Verifier([_FakeCollector({IdentifierType.USERNAME.value})])
    result = verifier.verify(
        [
            ProposedIdentifier(
                kind="username",
                value="alice",
                evidence_refs=(sha8,),
            )
        ],
        existing_traces=[trace],
        visited={seed},
    )
    assert result.accepted == ()
    assert "already investigated" in result.rejected[0].reason


def test_verifier_rejects_proposal_with_no_evidence() -> None:
    seed = Identifier(type=IdentifierType.USERNAME, value="seed")
    trace = _trace(seed, {"x": 1})

    verifier = Verifier([_FakeCollector({IdentifierType.DOMAIN.value})])
    result = verifier.verify(
        [ProposedIdentifier(kind="domain", value="alice.dev", evidence_refs=())],
        existing_traces=[trace],
        visited={seed},
    )
    assert result.accepted == ()
    assert "cites no evidence" in result.rejected[0].reason


def test_verifier_rejects_unmatched_evidence_prefix() -> None:
    seed = Identifier(type=IdentifierType.USERNAME, value="seed")
    trace = _trace(seed, {"x": 1})

    verifier = Verifier([_FakeCollector({IdentifierType.DOMAIN.value})])
    result = verifier.verify(
        [
            ProposedIdentifier(
                kind="domain",
                value="alice.dev",
                evidence_refs=("ffffffff",),
            )
        ],
        existing_traces=[trace],
        visited={seed},
    )
    assert result.accepted == ()
    assert "no cited evidence matches" in result.rejected[0].reason


def test_verifier_accepts_evidence_with_ev_prefix() -> None:
    """LLMs sometimes echo the citation format verbatim ('ev:abcdef12')."""
    seed = Identifier(type=IdentifierType.USERNAME, value="seed")
    trace = _trace(seed, {"x": 1})
    sha8 = trace.evidence.payload_sha256[:8]

    verifier = Verifier([_FakeCollector({IdentifierType.DOMAIN.value})])
    result = verifier.verify(
        [
            ProposedIdentifier(
                kind="domain",
                value="alice.dev",
                evidence_refs=(f"ev:{sha8}",),
            )
        ],
        existing_traces=[trace],
        visited={seed},
    )
    assert len(result.accepted) == 1


def test_verifier_dedupes_within_a_batch() -> None:
    seed = Identifier(type=IdentifierType.USERNAME, value="seed")
    trace = _trace(seed, {"x": 1})
    sha8 = trace.evidence.payload_sha256[:8]

    verifier = Verifier([_FakeCollector({IdentifierType.DOMAIN.value})])
    result = verifier.verify(
        [
            ProposedIdentifier(kind="domain", value="alice.dev", evidence_refs=(sha8,)),
            ProposedIdentifier(kind="domain", value="alice.dev", evidence_refs=(sha8,)),
        ],
        existing_traces=[trace],
        visited={seed},
    )
    assert len(result.accepted) == 1
    assert len(result.rejected) == 1
    assert "duplicate" in result.rejected[0].reason
