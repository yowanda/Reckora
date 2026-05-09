import { formatAbsolute, formatRelativeTime } from "@/lib/format";

import { Section } from "./Section";
import { identifierTypeLabel, sourceMeta } from "./sources";
import type { RawTimelineEntry } from "./types";

/**
 * Chronological projection of every Trace's evidence — one row per
 * entry, matching the shape produced by `build_timeline()` on the
 * backend. Replaces the plain markdown bullet list under the
 * `## Timeline` heading.
 */
export function Timeline({ entries }: { entries: RawTimelineEntry[] }) {
  return (
    <Section
      title="Timeline"
      meta={`${entries.length} ${entries.length === 1 ? "event" : "events"}`}
    >
      {entries.length === 0 ? (
        <p className="text-2xs text-fg-dim">No events.</p>
      ) : (
        <ol className="space-y-2">
          {entries.map((entry) => {
            const meta = sourceMeta(entry.source);
            const shortHash = entry.evidence_sha256.slice(0, 16);
            return (
              <li
                key={entry.evidence_sha256}
                className="flex flex-col gap-1 rounded border border-ink-line bg-ink-subtle/40 px-3 py-2 text-xs sm:flex-row sm:items-baseline sm:gap-3"
              >
                <time
                  className="shrink-0 font-mono text-2xs text-fg-muted"
                  dateTime={entry.timestamp}
                  title={formatAbsolute(entry.timestamp)}
                >
                  {formatRelativeTime(entry.timestamp)}
                </time>
                <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
                  <span className="font-semibold text-fg">{meta.label}</span>
                  <span className="font-mono text-2xs uppercase tracking-[0.1em] text-fg-dim">
                    {identifierTypeLabel(entry.identifier_type)}
                  </span>
                  <span className="font-mono text-fg [overflow-wrap:anywhere]">
                    {entry.identifier_value}
                  </span>
                </div>
                <div className="flex shrink-0 items-baseline gap-2 sm:ml-auto">
                  <span
                    className="font-mono text-2xs text-fg-dim"
                    title={entry.evidence_sha256}
                  >
                    sha {shortHash}…
                  </span>
                  <a
                    href={entry.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-2xs text-accent underline-offset-2 hover:underline"
                  >
                    source
                  </a>
                  {entry.archive_url ? (
                    <a
                      href={entry.archive_url}
                      target="_blank"
                      rel="noreferrer"
                      className="font-mono text-2xs text-accent underline-offset-2 hover:underline"
                    >
                      archive
                    </a>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Section>
  );
}
