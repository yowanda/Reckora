import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import type { SavedDossierSummary } from "@/api/types";
import { formatRelativeTime, shortId } from "@/lib/format";

interface SavedDossierListProps {
  items: SavedDossierSummary[];
  rowAction?: (subject: SavedDossierSummary) => ReactNode;
  rowChip?: (subject: SavedDossierSummary) => ReactNode;
}

/**
 * Reusable rendering for the three "/me/*" lists (Pinned, Watching,
 * potentially Mentions in the future) that all surface a list of
 * `SavedDossierSummary` rows. Page-level concerns (filters, empty
 * state, loading) live on the page; row visuals are unified here.
 */
export function SavedDossierList({
  items,
  rowAction,
  rowChip,
}: SavedDossierListProps) {
  return (
    <ul className="divide-y divide-ink-line overflow-hidden rounded-lg border border-ink-line bg-ink-panel">
      {items.map((subject) => (
        <li
          key={subject.id}
          className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-ink-subtle/60"
        >
          <div className="min-w-0 flex-1">
            <Link to={`/subjects/${subject.id}`} className="block">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="rounded border border-ink-line bg-ink-subtle px-1.5 py-0.5 font-mono text-2xs uppercase tracking-[0.12em] text-fg-muted">
                  {subject.seed_identifier.type}
                </span>
                <span className="truncate font-medium text-fg">
                  {subject.seed_identifier.value}
                </span>
                {rowChip ? rowChip(subject) : null}
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-fg-dim">
                <span className="font-mono uppercase tracking-[0.08em]">
                  {shortId(subject.id)}
                </span>
                <span>·</span>
                <span>created {formatRelativeTime(subject.created_at)}</span>
              </div>
            </Link>
          </div>
          {rowAction ? rowAction(subject) : null}
        </li>
      ))}
    </ul>
  );
}
