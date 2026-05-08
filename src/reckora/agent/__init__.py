"""Phase 4 — autonomous AgentLoop.

The agent layer drives Reckora's collect → correlate pipeline recursively.
Starting from a seed identifier it:

1. Runs the orchestrator once to bootstrap a working set of traces / edges.
2. Asks the AI reasoning layer to propose follow-up identifiers worth
   investigating, each citing the evidence that justifies it
   (``ev:<8-hex>`` payload prefixes).
3. Runs the proposed plan through a rule-based ``Verifier`` that rejects
   anything malformed, unsupported by Reckora's collectors, or already
   visited — the AI is allowed to be wrong, the rules are not.
4. Hands the surviving identifiers to the orchestrator, re-correlates the
   full trace set, and retains only the ones whose strongest correlation
   edge to the prior graph is at or above the configured confidence
   floor. The floor is the second gate: even an AI-proposed identifier
   the verifier accepted is dropped if rule-based correlation cannot
   actually link it back to the existing subject.

This is the "AI proposes, rules dispose" pattern called out in the
ROADMAP for Phase 4 — the LLM expands the search space, the deterministic
correlation engine decides what stays. The reasoning layer is wired
through :class:`reckora.reasoning.client.ReasoningClient`, so the loop
runs equally well on an ``OPENAI_API_KEY`` or on a ChatGPT OAuth login
landed in Phase 4 Path B.
"""

from __future__ import annotations

from .loop import AgentLoop, AgentLoopResult, AgentTranscriptStep
from .verifier import (
    ProposedIdentifier,
    VerificationResult,
    Verifier,
    VerifierRejection,
    parse_plan,
)

__all__ = [
    "AgentLoop",
    "AgentLoopResult",
    "AgentTranscriptStep",
    "ProposedIdentifier",
    "VerificationResult",
    "Verifier",
    "VerifierRejection",
    "parse_plan",
]
