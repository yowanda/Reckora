import { Section } from "./Section";
import { fieldLabel, identifierTypeLabel } from "./sources";
import type { RawEdge } from "./types";

/**
 * Correlation edges between identifiers — replaces the markdown
 * `## Correlation edges` block. Confidence is rendered both as a
 * percentage and a slim bar so a glance at the section conveys
 * relative strength across rows.
 */
export function Edges({ edges }: { edges: RawEdge[] }) {
  return (
    <Section
      title="Correlation edges"
      meta={`${edges.length} ${edges.length === 1 ? "edge" : "edges"}`}
    >
      {edges.length === 0 ? (
        <p className="text-2xs text-fg-dim">No edges.</p>
      ) : (
        <ul className="space-y-3">
          {edges.map((edge, i) => (
            <li
              key={i}
              className="rounded border border-ink-line bg-ink-subtle/40 p-3"
            >
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs">
                <IdentifierBadge
                  type={edge.source.type}
                  value={edge.source.value}
                />
                <span aria-hidden className="text-fg-dim">
                  ↔
                </span>
                <IdentifierBadge
                  type={edge.target.type}
                  value={edge.target.value}
                />
                <span className="ml-auto rounded border border-accent/30 bg-accent-soft px-1.5 py-0.5 font-mono text-2xs uppercase tracking-[0.12em] text-accent">
                  {fieldLabel(edge.kind)}
                </span>
              </div>
              <ConfidenceBar value={edge.confidence} />
              {edge.reasons.length > 0 ? (
                <ul className="mt-2 space-y-0.5 text-2xs text-fg-muted">
                  {edge.reasons.map((reason, j) => (
                    <li key={j} className="flex gap-2 leading-relaxed">
                      <span aria-hidden className="text-fg-dim">
                        ·
                      </span>
                      <span className="[overflow-wrap:anywhere]">{reason}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
              {edge.supporting_evidence.length > 0 ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {edge.supporting_evidence.map((sha) => (
                    <code
                      key={sha}
                      title={sha}
                      className="rounded border border-ink-line bg-ink-panel px-1.5 py-0.5 font-mono text-2xs text-fg-muted"
                    >
                      {sha.slice(0, 16)}…
                    </code>
                  ))}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function IdentifierBadge({ type, value }: { type: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded border border-ink-line bg-ink-panel px-1.5 py-0.5">
      <span className="font-mono text-2xs uppercase tracking-[0.12em] text-fg-dim">
        {identifierTypeLabel(type)}
      </span>
      <span className="font-mono text-xs text-fg [overflow-wrap:anywhere]">
        {value}
      </span>
    </span>
  );
}

function ConfidenceBar({ value }: { value: number }) {
  const clamped = Math.max(0, Math.min(1, value));
  const percent = Math.round(clamped * 100);
  return (
    <div className="mt-2 flex items-center gap-2 text-2xs text-fg-muted">
      <div
        className="h-1 flex-1 overflow-hidden rounded bg-ink-line"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full rounded bg-accent"
          style={{ width: `${percent}%` }}
        />
      </div>
      <span className="font-mono">{percent}%</span>
    </div>
  );
}
