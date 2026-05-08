"""Plan parsing and rule-based verification for Phase 4 AgentLoop.

The verifier sits between the AI reasoning layer (which proposes follow-up
identifiers) and the orchestrator (which would otherwise execute every
proposal blindly). It refuses any proposal that:

* Fails to parse against the strict JSON schema we ask the LLM to emit.
* Names an identifier kind Reckora doesn't model (``IdentifierType``).
* Lacks a non-empty ``value`` or invents an identifier we already
  investigated this run.
* Is unsupported by any of the orchestrator's collectors — running an
  identifier no collector can resolve is a guaranteed empty trace, so we
  short-circuit instead of paying the cost.
* Cites no evidence, or cites payload SHA prefixes that don't appear in
  the existing trace set. This is the "evidence-bounded" guarantee the
  ROADMAP layers depend on: the AI cannot expand the search space onto
  identifiers it pulled out of thin air.

Each rejection records a human-readable reason so the agent transcript
in :class:`reckora.agent.loop.AgentTranscriptStep` can show the user
*why* a proposal was dropped — debugging an autonomous agent without
that visibility is a nightmare.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from ..collectors.base import Collector
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType


@dataclass(frozen=True)
class ProposedIdentifier:
    """A single AI-proposed identifier, parsed but **not** yet verified.

    The fields mirror the JSON shape we request from the LLM. ``rationale``
    is informational (we record it on the transcript so reviewers can see
    *why* the AI thought a proposal was promising); ``evidence_refs`` is
    load-bearing — the verifier rejects proposals whose citations don't
    line up with existing evidence.
    """

    kind: str
    value: str
    rationale: str = ""
    evidence_refs: tuple[str, ...] = ()

    def to_identifier(self) -> Identifier:
        """Coerce ``kind``/``value`` into an :class:`Identifier`.

        Raises ``ValueError`` if ``kind`` is not a known
        :class:`IdentifierType`. Callers should funnel proposals
        through :meth:`Verifier.verify` rather than calling this
        directly so the rejection ends up on the transcript instead of
        crashing the loop.
        """
        identifier_type = IdentifierType(self.kind)
        return Identifier(type=identifier_type, value=self.value)


@dataclass(frozen=True)
class VerifierRejection:
    """A single proposal the verifier refused to forward to the orchestrator."""

    proposal: ProposedIdentifier
    reason: str


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of running a proposal batch through :class:`Verifier`.

    ``accepted`` carries the ``Identifier`` instances ready for the
    orchestrator; ``rejected`` keeps the original proposal plus a
    human-readable reason so the transcript can explain the drop.
    """

    accepted: tuple[Identifier, ...] = ()
    accepted_proposals: tuple[ProposedIdentifier, ...] = ()
    rejected: tuple[VerifierRejection, ...] = field(default_factory=tuple)


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_plan(raw: str) -> list[ProposedIdentifier]:
    """Extract a list of :class:`ProposedIdentifier` from raw LLM output.

    The LLM is asked to emit JSON of the form::

        {"plan": [
            {"kind": "domain", "value": "alice.example", "rationale": "...",
             "evidence_refs": ["a1b2c3d4"]},
            ...
        ]}

    We tolerate Markdown fenced code blocks (\\`\\`\\`json ... \\`\\`\\`) since
    chat-tuned models love wrapping output even when asked not to.
    Returns ``[]`` on any parse failure rather than raising — the
    caller treats an unparseable response as "no plan", which is the
    right behaviour for a non-deterministic upstream.
    """
    if not raw or not raw.strip():
        return []

    candidates = [raw]
    for match in _FENCE_RE.finditer(raw):
        candidates.append(match.group(1).strip())

    for candidate in candidates:
        proposals = _try_parse(candidate)
        if proposals is not None:
            return proposals
    return []


def _try_parse(raw: str) -> list[ProposedIdentifier] | None:
    """Attempt to decode ``raw`` and pull a ``plan`` array out of it.

    Returns ``None`` if the JSON didn't decode at all (so callers can
    try other candidates), and ``[]`` if it decoded but contained no
    well-formed plan entries.
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if isinstance(decoded, list):
        items: list[object] = decoded
    elif isinstance(decoded, dict):
        plan = decoded.get("plan") or decoded.get("proposals") or []
        if not isinstance(plan, list):
            return []
        items = plan
    else:
        return []

    proposals: list[ProposedIdentifier] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind") or entry.get("type")
        value = entry.get("value")
        if not isinstance(kind, str) or not isinstance(value, str):
            continue
        rationale_raw = entry.get("rationale") or entry.get("reason") or ""
        rationale = rationale_raw if isinstance(rationale_raw, str) else ""
        refs_raw = entry.get("evidence_refs") or entry.get("evidence") or []
        refs: list[str] = []
        if isinstance(refs_raw, list):
            for ref in refs_raw:
                if isinstance(ref, str):
                    refs.append(ref.strip())
        proposals.append(
            ProposedIdentifier(
                kind=kind.strip(),
                value=value.strip(),
                rationale=rationale.strip(),
                evidence_refs=tuple(refs),
            )
        )
    return proposals


# Evidence prefixes are SHA-256 hex; we accept anything between the
# 8-char short form the prompts request and the full 64-char digest.
_EVIDENCE_REF_RE = re.compile(r"^[0-9a-f]{8,64}$")


class Verifier:
    """Rule-based gate that decides which proposals reach the orchestrator.

    The verifier is **stateless** — pass it the orchestrator's collector
    list once at construction and call :meth:`verify` for each LLM
    response. Visited identifiers and the existing trace set are passed
    in per-call so the AgentLoop can update them between iterations
    without re-instantiating the verifier.
    """

    def __init__(self, collectors: Iterable[Collector]) -> None:
        self._collectors = list(collectors)

    def verify(
        self,
        proposals: Iterable[ProposedIdentifier],
        *,
        existing_traces: Iterable[Trace],
        visited: Iterable[Identifier],
    ) -> VerificationResult:
        """Run a batch of proposals through every gate.

        Each accepted proposal contributes one :class:`Identifier` to
        ``accepted``; everything else lands in ``rejected`` with a
        reason. Duplicate accepted proposals (same identifier) are
        collapsed so the orchestrator never sees the same identifier
        twice in a single iteration.
        """
        evidence_prefixes = _evidence_prefixes(existing_traces)
        visited_set = set(visited)
        accepted: list[Identifier] = []
        accepted_proposals: list[ProposedIdentifier] = []
        rejected: list[VerifierRejection] = []
        seen_in_batch: set[Identifier] = set()

        for proposal in proposals:
            reason = self._reject_reason(
                proposal,
                evidence_prefixes=evidence_prefixes,
                visited=visited_set,
                seen_in_batch=seen_in_batch,
            )
            if reason is not None:
                rejected.append(VerifierRejection(proposal=proposal, reason=reason))
                continue
            identifier = proposal.to_identifier()
            seen_in_batch.add(identifier)
            accepted.append(identifier)
            accepted_proposals.append(proposal)

        return VerificationResult(
            accepted=tuple(accepted),
            accepted_proposals=tuple(accepted_proposals),
            rejected=tuple(rejected),
        )

    def _reject_reason(
        self,
        proposal: ProposedIdentifier,
        *,
        evidence_prefixes: set[str],
        visited: set[Identifier],
        seen_in_batch: set[Identifier],
    ) -> str | None:
        """Return a human-readable rejection reason, or ``None`` on accept."""
        if not proposal.value:
            return "empty identifier value"

        try:
            identifier = proposal.to_identifier()
        except ValueError:
            valid = ", ".join(e.value for e in IdentifierType)
            return f"unknown identifier kind {proposal.kind!r} (expected one of: {valid})"

        if identifier in visited:
            return "identifier already investigated this run"
        if identifier in seen_in_batch:
            return "duplicate proposal within the same plan"

        if not any(c.supports(identifier) for c in self._collectors):
            return f"no collector supports {proposal.kind} identifiers"

        if not proposal.evidence_refs:
            return "proposal cites no evidence"
        if not _has_matching_evidence(proposal.evidence_refs, evidence_prefixes):
            return "no cited evidence matches a known trace's payload SHA"

        return None


def _evidence_prefixes(traces: Iterable[Trace]) -> set[str]:
    """Build the set of canonical short / long SHA prefixes for the working set.

    We index *every* prefix length the prompts allow (>=8 hex) so an LLM
    that copies the full 64-char digest verifies just as cleanly as one
    that uses the 8-char short form Reckora's reports default to.
    """
    prefixes: set[str] = set()
    for trace in traces:
        digest = trace.evidence.payload_sha256.lower()
        prefixes.add(digest)
        # Index every length from 8 to len(digest) so any reasonable
        # truncation the LLM produces matches with O(1) lookup.
        for n in range(8, len(digest) + 1):
            prefixes.add(digest[:n])
    return prefixes


def _has_matching_evidence(refs: Iterable[str], prefixes: set[str]) -> bool:
    """``True`` iff at least one ``ref`` exactly matches an indexed prefix."""
    for ref in refs:
        cleaned = ref.lower().strip()
        # Strip a leading ``ev:`` if the LLM echoed the prompt's
        # citation format verbatim ("ev:a1b2c3d4").
        if cleaned.startswith("ev:"):
            cleaned = cleaned[len("ev:") :]
        if not cleaned:
            continue
        if not _EVIDENCE_REF_RE.match(cleaned):
            continue
        if cleaned in prefixes:
            return True
    return False
