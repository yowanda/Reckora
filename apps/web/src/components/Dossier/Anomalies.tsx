import { Section } from "./Section";
import { fieldLabel } from "./sources";
import type { AnomalySeverity, RawAnomaly } from "./types";

/**
 * Anomaly findings — replaces the markdown
 * `- **HIGH** · `kind` — message (`hash…`)` bullet style with a
 * panel-per-finding layout, severity chip up front, and the supporting
 * evidence hashes rendered as monospace tags.
 */
export function Anomalies({ anomalies }: { anomalies: RawAnomaly[] }) {
  return (
    <Section
      title="Anomalies"
      meta={`${anomalies.length} ${anomalies.length === 1 ? "finding" : "findings"}`}
    >
      {anomalies.length === 0 ? (
        <p className="text-2xs text-fg-dim">No anomalies detected.</p>
      ) : (
        <ul className="space-y-2">
          {anomalies.map((anomaly, i) => (
            <li
              key={`${anomaly.kind}:${i}`}
              className="rounded border border-ink-line bg-ink-subtle/40 p-3"
            >
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
                <SeverityChip severity={anomaly.severity} />
                <span className="font-mono text-2xs uppercase tracking-[0.1em] text-fg-dim">
                  {fieldLabel(anomaly.kind)}
                </span>
              </div>
              <p className="mt-1.5 text-xs leading-relaxed text-fg [overflow-wrap:anywhere]">
                {anomaly.message}
              </p>
              {anomaly.supporting_evidence.length > 0 ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {anomaly.supporting_evidence.map((sha) => (
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

function SeverityChip({ severity }: { severity: AnomalySeverity | string }) {
  const tone = SEVERITY_TONE[severity as AnomalySeverity] ?? SEVERITY_TONE.low;
  return (
    <span
      className={`rounded border ${tone} px-1.5 py-0.5 font-mono text-2xs uppercase tracking-[0.12em]`}
    >
      {String(severity).toUpperCase()}
    </span>
  );
}

const SEVERITY_TONE: Record<AnomalySeverity, string> = {
  low: "border-ink-line bg-ink-subtle text-fg-muted",
  medium: "border-alert/40 bg-alert-soft text-alert",
  high: "border-danger/40 bg-danger-soft text-danger",
};
