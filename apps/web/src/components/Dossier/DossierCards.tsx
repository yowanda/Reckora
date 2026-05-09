import { AiBlock } from "./AiBlock";
import { Anchor } from "./Anchor";
import { Anomalies } from "./Anomalies";
import { Brief } from "./Brief";
import { Edges } from "./Edges";
import { Section } from "./Section";
import { Timeline } from "./Timeline";
import { TraceCard } from "./TraceCard";
import { sourceMeta } from "./sources";
import type { DossierView, RawTrace } from "./types";

/**
 * Top-level "cards" view of a dossier — the structured replacement
 * for `react-markdown` rendering of the `?format=md` blob.
 *
 * Each section maps onto the corresponding key of the
 * {@link DossierView} (which is just `to_dossier_dict()` from
 * `src/reckora/reports/json_export.py` typed properly), so adding a
 * new top-level field on the backend only requires a new section
 * component here — no markdown stitching, no string parsing.
 */
export function DossierCards({ view }: { view: DossierView }) {
  const traces = sortTraces(view.traces);
  return (
    <div className="space-y-4">
      <Brief view={view} />
      <Section
        title="Traces"
        meta={`${traces.length} ${traces.length === 1 ? "source" : "sources"}`}
      >
        {traces.length === 0 ? (
          <p className="text-2xs text-fg-dim">No traces collected.</p>
        ) : (
          <div className="space-y-3">
            {traces.map((trace, i) => (
              <TraceCard key={traceKey(trace, i)} trace={trace} />
            ))}
          </div>
        )}
      </Section>
      <Timeline entries={view.timeline} />
      <Anomalies anomalies={view.anomalies} />
      <Edges edges={view.edges} />
      <AiBlock ai={view.ai} />
      <Anchor anchor={view.anchor} />
    </div>
  );
}

/**
 * Group traces by source for a more scannable card list — keeps the
 * relative ordering inside each group stable so identical inputs
 * always render in the same order across reloads.
 */
function sortTraces(traces: RawTrace[]): RawTrace[] {
  return [...traces].sort((a, b) => {
    const aLabel = sourceMeta(a.source).label.toLowerCase();
    const bLabel = sourceMeta(b.source).label.toLowerCase();
    if (aLabel === bLabel) {
      return a.identifier.value.localeCompare(b.identifier.value);
    }
    return aLabel.localeCompare(bLabel);
  });
}

function traceKey(trace: RawTrace, index: number): string {
  return `${trace.source}:${trace.identifier.type}:${trace.identifier.value}:${trace.evidence.payload_sha256}:${index}`;
}
