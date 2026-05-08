"""Phase 4 AgentLoop: AI proposes, rules dispose.

The loop is intentionally small — it stitches together components that
already exist (the orchestrator, the correlation engine, the reasoning
client) rather than re-implementing any of them. The novelty is the
control flow:

    initial collect -> [LLM proposes plan -> rule-based verify ->
    orchestrator collects -> correlation engine re-scores ->
    confidence-floor gate -> retain or drop] * N

Two gates protect us from an LLM that is wrong or adversarial:

* The :class:`Verifier` rejects proposals that don't parse, name unknown
  identifier kinds, or cite no real evidence.
* The post-collection confidence floor drops *accepted-by-the-verifier*
  identifiers whose strongest correlation edge to the existing graph is
  below ``confidence_floor``. Even if the LLM correctly cited evidence,
  the rule-based engine still has to *prove* the link.

The loop terminates as soon as any of:

* A new iteration produces no accepted proposals.
* No accepted identifier survives the confidence-floor gate.
* The reasoning client returns an unparseable plan.
* ``max_iterations`` is reached.

Termination is "successful" in either case — the agent simply ran out
of evidence-grounded follow-ups, which is exactly when we want it to
stop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..correlation.embeddings import BioEmbedder
from ..correlation.engine import correlate
from ..models.entity import Edge, Identifier, Subject, Trace
from ..orchestrator import Orchestrator
from ..reasoning.client import ReasoningClient
from ..reasoning.summarize import format_edge, format_trace
from .prompts import AGENT_SYSTEM, PROPOSE_USER_TEMPLATE
from .verifier import (
    ProposedIdentifier,
    VerificationResult,
    Verifier,
    VerifierRejection,
    parse_plan,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentTranscriptStep:
    """One iteration of the AgentLoop, fully reified for transcripts.

    Carries enough state to render the agent's reasoning to a human
    reviewer: the raw plan it proposed, every rejection the verifier
    issued, every identifier the confidence-floor gate dropped, and
    the new traces / edges retained.
    """

    iteration: int
    raw_plan: str
    proposals: tuple[ProposedIdentifier, ...]
    accepted: tuple[Identifier, ...]
    rejected: tuple[VerifierRejection, ...]
    confidence_dropped: tuple[Identifier, ...]
    retained: tuple[Identifier, ...]
    new_traces: tuple[Trace, ...]


@dataclass(frozen=True)
class AgentLoopResult:
    """Final output of an :class:`AgentLoop` run.

    Mirrors the orchestrator's ``investigate`` return shape so callers
    can swap one for the other, with the addition of a transcript
    capturing each iteration's plan / verifier output for audit.
    """

    subject: Subject
    traces: tuple[Trace, ...]
    edges: tuple[Edge, ...]
    transcript: tuple[AgentTranscriptStep, ...] = field(default_factory=tuple)


class AgentLoop:
    """Recursive identifier expansion driven by an LLM and gated by rules.

    Construction is intentionally explicit: pass the same orchestrator,
    reasoning client, and (optionally) bio embedder you'd use for a
    one-shot investigation. The loop reuses them rather than owning
    parallel copies.
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        reasoning_client: ReasoningClient,
        *,
        max_iterations: int = 3,
        confidence_floor: float = 0.5,
        max_proposals_per_iteration: int = 5,
        bio_embedder: BioEmbedder | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        if not 0.0 <= confidence_floor <= 1.0:
            raise ValueError("confidence_floor must lie in [0.0, 1.0]")
        if max_proposals_per_iteration < 1:
            raise ValueError("max_proposals_per_iteration must be >= 1")
        self._orchestrator = orchestrator
        self._client = reasoning_client
        self._max_iterations = max_iterations
        self._confidence_floor = confidence_floor
        self._max_proposals = max_proposals_per_iteration
        self._bio_embedder = bio_embedder
        self._verifier = Verifier(_collectors_of(orchestrator))

    async def run(self, seed: Identifier) -> AgentLoopResult:
        """Drive the recursive expansion to a fixpoint or ``max_iterations``."""
        subject, traces, edges = await self._orchestrator.investigate(seed)
        # Re-correlate with the bio embedder if the caller wired one in.
        # The orchestrator's first pass uses the dependency-free lexical
        # path; the agent layer is where embeddings come into play.
        if self._bio_embedder is not None:
            edges = correlate(traces, bio_embedder=self._bio_embedder)

        visited: set[Identifier] = {seed}
        for trace in traces:
            visited.add(trace.identifier)

        transcript: list[AgentTranscriptStep] = []
        for iteration in range(1, self._max_iterations + 1):
            step = await self._iterate(
                iteration=iteration,
                seed=seed,
                traces=traces,
                edges=edges,
                visited=visited,
            )
            if step is None:
                break
            transcript.append(step)
            if not step.retained:
                # Either the verifier killed every proposal or every
                # accepted candidate failed the confidence-floor gate.
                break
            traces = list(traces) + list(step.new_traces)
            edges = correlate(traces, bio_embedder=self._bio_embedder)

        all_identifiers = _ordered_identifiers(seed, visited)
        subject = Subject(
            id=subject.id,
            seed_identifier=seed,
            identifiers=all_identifiers,
            traces=list(traces),
        )
        return AgentLoopResult(
            subject=subject,
            traces=tuple(traces),
            edges=tuple(edges),
            transcript=tuple(transcript),
        )

    async def _iterate(
        self,
        *,
        iteration: int,
        seed: Identifier,
        traces: list[Trace],
        edges: list[Edge],
        visited: set[Identifier],
    ) -> AgentTranscriptStep | None:
        """Run one (propose -> verify -> collect -> gate) round.

        Returns ``None`` when the LLM produced no parseable plan — that
        signals "stop the loop" without recording an empty transcript
        entry. Otherwise returns the iteration's transcript step;
        ``step.retained`` empty means the loop should stop.
        """
        user = self._build_user_prompt(
            seed=seed,
            iteration=iteration,
            visited=visited,
            traces=traces,
            edges=edges,
        )
        try:
            raw_plan = await self._client.complete(AGENT_SYSTEM, user)
        except Exception as exc:
            log.exception("agent reasoning step failed: %s", exc)
            return None

        proposals = parse_plan(raw_plan)
        if not proposals:
            return None

        # Cap proposals before verification so a runaway LLM can't
        # exhaust the orchestrator. The verifier is cheap, but
        # collectors hit the network.
        proposals = proposals[: self._max_proposals]

        verification = self._verifier.verify(
            proposals,
            existing_traces=traces,
            visited=visited,
        )
        if not verification.accepted:
            return _empty_step(
                iteration=iteration,
                raw_plan=raw_plan,
                proposals=proposals,
                verification=verification,
            )

        new_traces = await self._collect(verification.accepted)
        retained, dropped = self._apply_confidence_floor(
            candidates=verification.accepted,
            new_traces=new_traces,
            existing_traces=traces,
        )

        # Update visited with retained identifiers so future iterations
        # don't re-propose them. Drop the new traces whose identifier
        # didn't pass the floor.
        retained_set = set(retained)
        kept_traces = tuple(t for t in new_traces if t.identifier in retained_set)
        for ident in retained_set:
            visited.add(ident)
        for trace in kept_traces:
            visited.add(trace.identifier)

        return AgentTranscriptStep(
            iteration=iteration,
            raw_plan=raw_plan,
            proposals=tuple(proposals),
            accepted=verification.accepted,
            rejected=verification.rejected,
            confidence_dropped=dropped,
            retained=retained,
            new_traces=kept_traces,
        )

    def _build_user_prompt(
        self,
        *,
        seed: Identifier,
        iteration: int,
        visited: set[Identifier],
        traces: list[Trace],
        edges: list[Edge],
    ) -> str:
        """Render the user-side template with the current investigation state."""
        identifiers = sorted(str(i) for i in visited)
        return PROPOSE_USER_TEMPLATE.format(
            seed=str(seed),
            iteration=iteration,
            max_iterations=self._max_iterations,
            identifiers="\n".join(f"- {i}" for i in identifiers) or "(none)",
            n_traces=len(traces),
            traces="\n".join(format_trace(t) for t in traces) or "(none)",
            n_edges=len(edges),
            edges="\n".join(format_edge(e) for e in edges) or "(none)",
        )

    async def _collect(
        self,
        identifiers: tuple[Identifier, ...],
    ) -> list[Trace]:
        """Run the orchestrator's collectors against each accepted identifier.

        We invoke the orchestrator once per identifier so a failure in
        one branch can't void the whole iteration. The orchestrator
        already swallows per-collector exceptions, so this layer only
        has to defend against the (unexpected) case where the whole
        ``investigate`` call raises.
        """
        all_traces: list[Trace] = []
        for ident in identifiers:
            try:
                _, ident_traces, _ = await self._orchestrator.investigate(ident)
            except Exception:
                log.exception("agent collection step failed on %s", ident)
                continue
            all_traces.extend(ident_traces)
        return all_traces

    def _apply_confidence_floor(
        self,
        *,
        candidates: tuple[Identifier, ...],
        new_traces: list[Trace],
        existing_traces: list[Trace],
    ) -> tuple[tuple[Identifier, ...], tuple[Identifier, ...]]:
        """Keep only candidates linked back to the prior graph above the floor.

        The check runs the *full* correlation engine over (existing +
        new) traces and inspects every edge involving the candidate.
        We require at least one edge with confidence >=
        ``confidence_floor`` whose other endpoint is *not* one of the
        new candidates — i.e. the candidate must connect to the
        existing graph, not just to its sibling in the same plan.
        """
        candidate_set = set(candidates)
        if not candidate_set:
            return ((), ())

        merged = list(existing_traces) + list(new_traces)
        merged_edges = correlate(merged, bio_embedder=self._bio_embedder)

        retained: list[Identifier] = []
        dropped: list[Identifier] = []
        for ident in candidates:
            if _passes_floor(
                ident,
                edges=merged_edges,
                floor=self._confidence_floor,
                candidate_set=candidate_set,
            ):
                retained.append(ident)
            else:
                dropped.append(ident)
        return tuple(retained), tuple(dropped)


def _passes_floor(
    ident: Identifier,
    *,
    edges: list[Edge],
    floor: float,
    candidate_set: set[Identifier],
) -> bool:
    """``True`` iff ``ident`` has an edge >= floor to a non-candidate identifier."""
    for edge in edges:
        if edge.confidence < floor:
            continue
        if edge.source == ident and edge.target not in candidate_set:
            return True
        if edge.target == ident and edge.source not in candidate_set:
            return True
    return False


def _empty_step(
    *,
    iteration: int,
    raw_plan: str,
    proposals: list[ProposedIdentifier],
    verification: VerificationResult,
) -> AgentTranscriptStep:
    """Render an iteration that produced zero accepted identifiers.

    Kept verbose on purpose: the rejection list is the most useful
    transcript field when the agent terminates without expanding the
    graph, since it tells reviewers *why* the LLM failed verification.
    """
    return AgentTranscriptStep(
        iteration=iteration,
        raw_plan=raw_plan,
        proposals=tuple(proposals),
        accepted=(),
        rejected=verification.rejected,
        confidence_dropped=(),
        retained=(),
        new_traces=(),
    )


def _collectors_of(orchestrator: Orchestrator) -> list:  # type: ignore[type-arg]
    """Pull the collector list off an orchestrator instance.

    The orchestrator stores collectors on a private attribute today;
    this helper is the single point that reaches in so a future
    refactor only has to update one site.
    """
    return list(orchestrator._collectors)


def _ordered_identifiers(seed: Identifier, visited: set[Identifier]) -> list[Identifier]:
    """Return the visited identifiers with the seed first, the rest sorted.

    The seed slot matters for downstream consumers (reports, persistence)
    that want to pin the entry-point identifier; the order of the
    remainder is purely for stability of test output.
    """
    rest = [i for i in visited if i != seed]
    rest.sort(key=lambda i: (i.type.value, i.value))
    return [seed, *rest]
