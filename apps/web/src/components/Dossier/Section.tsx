import type { ReactNode } from "react";

/**
 * Cyan-accented panel header used across every dossier section
 * (`Brief`, `Traces`, `Timeline`, `Anomalies`, `Edges`, `AI`, `Anchor`).
 *
 * Centralises the look so the section rail reads as a single
 * visual rhythm — uppercase tracking, hairline divider, optional
 * meta slot for counts ("12 traces") on the right.
 */
export function Section({
  title,
  meta,
  children,
}: {
  title: string;
  meta?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-ink-line bg-ink-panel">
      <header className="flex items-center justify-between gap-3 border-b border-ink-line bg-ink-subtle/40 px-4 py-2">
        <h2 className="text-2xs font-medium uppercase tracking-[0.2em] text-accent">
          {title}
        </h2>
        {meta !== undefined && meta !== null ? (
          <span className="font-mono text-2xs text-fg-dim">{meta}</span>
        ) : null}
      </header>
      <div className="space-y-3 p-4">{children}</div>
    </section>
  );
}
