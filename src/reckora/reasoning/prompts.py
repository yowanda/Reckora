"""Prompt templates for the Reckora AI reasoning layer.

The system prompt strictly bounds the LLM to evidence-grounded output. The
templates feed in *normalised* trace and edge rows — never raw HTML, never
scraped content directly. This is the seam that keeps Reckora's AI layer
honest: if a fact is not in the evidence rows, the LLM cannot truthfully
emit it.
"""

from __future__ import annotations

SYSTEM_BASE = """You are an OSINT investigation assistant for the Reckora system.
You operate STRICTLY over the verified evidence rows passed to you.

Output rules:
1. Never invent facts. If something is not in the evidence, do not claim it.
2. When you make a factual claim, cite the supporting evidence by its
   payload_sha256 short prefix (first 8 hex chars), e.g. (ev: a1b2c3d4).
3. Match confidence language to the per-edge confidence given:
   - >= 0.80 -> "strong"
   - 0.50-0.79 -> "moderate"
   - < 0.50 -> "weak"
4. Surface anomalies, gaps, and contradictions in the evidence explicitly.
5. Output Markdown with concise headings. No filler."""

SUMMARIZE_USER_TEMPLATE = """Investigation summary request.

Subject seed: {seed}
Identifiers gathered: {identifiers}

Traces ({n_traces}):
{traces}

Edges ({n_edges}):
{edges}

Produce:
- A tight investigative summary (4-8 sentences).
- A `Confidence` line stating overall confidence in the entity-resolution
  hypothesis (strong / moderate / weak / insufficient evidence).
- A `Gaps` bullet list of follow-up evidence that would tighten or refute the
  hypothesis."""

HYPOTHESIZE_USER_TEMPLATE = """Hypothesis-generation request.

Subject seed: {seed}
Identifiers gathered: {identifiers}

Traces ({n_traces}):
{traces}

Edges ({n_edges}):
{edges}

Propose 2-4 distinct investigative hypotheses about the subject's identity,
activity, or context. For each:
- State the hypothesis in one sentence.
- List the evidence that supports it (cite ev: prefixes).
- List the evidence that would refute it.
- Give an explicit confidence label (strong / moderate / weak).
Be terse."""
