import ReactMarkdown from "react-markdown";

import { Section } from "./Section";
import type { RawAi } from "./types";

/**
 * Renders the AI-reasoning block (`summary` + `hypotheses`).
 *
 * The fields are free-form strings populated by the agent loop, so we
 * keep markdown rendering for them — but we surface them as two
 * dedicated subsections instead of two unrelated `## AI summary` /
 * `## AI hypotheses` headings, so the analyst can ignore the block in
 * one go when the dossier wasn't run with `--ai`.
 */
export function AiBlock({ ai }: { ai: RawAi }) {
  const summary = nonEmpty(ai.summary);
  const hypotheses = nonEmpty(ai.hypotheses);
  if (summary === null && hypotheses === null) {
    return null;
  }
  return (
    <Section title="AI reasoning">
      {summary !== null ? (
        <div>
          <p className="mb-1.5 text-2xs font-medium uppercase tracking-[0.18em] text-fg-dim">
            Summary
          </p>
          <div className="prose prose-invert prose-sm max-w-none">
            <ReactMarkdown>{summary}</ReactMarkdown>
          </div>
        </div>
      ) : null}
      {hypotheses !== null ? (
        <div>
          <p className="mb-1.5 text-2xs font-medium uppercase tracking-[0.18em] text-fg-dim">
            Hypotheses
          </p>
          <div className="prose prose-invert prose-sm max-w-none">
            <ReactMarkdown>{hypotheses}</ReactMarkdown>
          </div>
        </div>
      ) : null}
    </Section>
  );
}

function nonEmpty(value: string | null | undefined): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed.length === 0 ? null : value;
}
