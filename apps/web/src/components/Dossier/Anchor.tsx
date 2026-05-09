import { formatAbsolute } from "@/lib/format";

import { Section } from "./Section";
import type { RawAnchor } from "./types";

/**
 * Cross-trace anchor block — Merkle root + OpenTimestamps receipts.
 *
 * Mirrors the markdown `## Cross-trace anchor` block but surfaces the
 * Merkle root in monospace and renders each calendar receipt as a
 * compact row, so the chain of custody stays readable when a dossier
 * has multiple calendars.
 */
export function Anchor({ anchor }: { anchor: RawAnchor | null }) {
  if (anchor === null) {
    return null;
  }
  const leaves = anchor.leaf_hashes?.length ?? 0;
  const receipts = anchor.receipts ?? [];
  return (
    <Section title="Cross-trace anchor" meta={`${leaves} leaves`}>
      <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-xs">
        <dt className="font-semibold text-fg-muted">Merkle root</dt>
        <dd className="min-w-0 font-mono text-fg [overflow-wrap:anywhere]">
          {anchor.merkle_root}
        </dd>
        <dt className="font-semibold text-fg-muted">Created</dt>
        <dd className="text-fg-muted">{formatAbsolute(anchor.created_at)}</dd>
      </dl>
      <div>
        <p className="mb-1.5 text-2xs font-medium uppercase tracking-[0.18em] text-fg-dim">
          Calendars
        </p>
        {receipts.length === 0 ? (
          <p className="text-2xs text-fg-dim">
            None responded — root preserved locally.
          </p>
        ) : (
          <ul className="space-y-1.5 text-xs">
            {receipts.map((receipt, i) => (
              <li
                key={`${receipt.calendar_url}:${i}`}
                className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5"
              >
                <a
                  href={receipt.calendar_url}
                  target="_blank"
                  rel="noreferrer"
                  className="font-mono text-accent underline-offset-2 hover:underline [overflow-wrap:anywhere]"
                >
                  {receipt.calendar_url}
                </a>
                <span className="text-2xs text-fg-dim">
                  submitted {formatAbsolute(receipt.submitted_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  );
}
