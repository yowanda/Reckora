import { formatRelativeTime } from "@/lib/format";

import { Section } from "./Section";
import { identifierTypeLabel } from "./sources";
import type { DossierView } from "./types";

/**
 * Top-of-dossier "brief" panel.
 *
 * The markdown export opens with a plain `# Reckora dossier — kind:value`
 * heading and an `_generated:_` line. We replace it with a structured
 * brief that surfaces the seed identifier, the resolved identifier
 * cluster and the dossier's own metadata (id, created-at) so the
 * analyst gets the same context without having to parse the heading.
 */
export function Brief({ view }: { view: DossierView }) {
  const seed = view.subject.seed_identifier;
  const identifiers = view.subject.identifiers ?? [];
  return (
    <Section
      title="Brief"
      meta={`${identifiers.length} ${identifiers.length === 1 ? "identifier" : "identifiers"}`}
    >
      <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
        <dt className="font-semibold text-fg-muted">Seed</dt>
        <dd className="min-w-0 [overflow-wrap:anywhere]">
          <span className="mr-2 rounded border border-accent/30 bg-accent-soft px-1.5 py-0.5 font-mono text-2xs uppercase tracking-[0.12em] text-accent">
            {identifierTypeLabel(seed.type)}
          </span>
          <span className="font-mono text-fg">{seed.value}</span>
        </dd>

        <dt className="font-semibold text-fg-muted">Subject</dt>
        <dd className="min-w-0 font-mono text-xs text-fg [overflow-wrap:anywhere]">
          {view.subject.id}
        </dd>

        <dt className="font-semibold text-fg-muted">Created</dt>
        <dd className="text-xs text-fg-muted" title={view.created_at}>
          {formatRelativeTime(view.created_at)}
        </dd>

        {view.owner_username ? (
          <>
            <dt className="font-semibold text-fg-muted">Owner</dt>
            <dd className="text-xs text-fg">{view.owner_username}</dd>
          </>
        ) : null}
      </dl>

      {identifiers.length > 0 ? (
        <div>
          <p className="mb-2 text-2xs font-medium uppercase tracking-[0.18em] text-fg-dim">
            Resolved identifiers
          </p>
          <ul className="flex flex-wrap gap-1.5">
            {identifiers.map((ident, i) => (
              <li
                key={`${ident.type}:${ident.value}:${i}`}
                className="flex items-center gap-1.5 rounded border border-ink-line bg-ink-subtle/60 px-2 py-1 text-xs"
              >
                <span className="font-mono text-2xs uppercase tracking-[0.12em] text-fg-dim">
                  {identifierTypeLabel(ident.type)}
                </span>
                <span className="font-mono text-fg [overflow-wrap:anywhere]">
                  {ident.value}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Section>
  );
}
