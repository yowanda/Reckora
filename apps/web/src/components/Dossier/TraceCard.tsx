import { formatRelativeTime } from "@/lib/format";

import { Avatar } from "./Avatar";
import { FieldTable } from "./FieldTable";
import type { RawTrace } from "./types";
import { fieldLabel, sourceMeta } from "./sources";

/**
 * Per-source trace card — the structured replacement for the old
 * markdown bullet list under `## Traces`.
 *
 * Layout mirrors the Maigret report design called out in the spec:
 *
 * - Avatar pinned to the **top** on mobile and the **left** rail on
 *   `sm:`+, sized 56px → 80px so it reads as a deliberate anchor next
 *   to the field table.
 * - Card header carries the human-readable platform label (e.g.
 *   `"GitHub"`, `"Social presence probe"`), the per-card identifier
 *   chip and the relative fetched-at timestamp.
 * - URL row sits *above* the field table with category tag chips
 *   (e.g. `social`, `infrastructure`) underneath, so the analyst can
 *   triage by source kind without parsing the field block.
 * - The 2-column field table renders below, filtering empty values and
 *   delegating URL detection / monospace IDs to {@link FieldTable}.
 *
 * Evidence metadata (sha256, archive URL, screenshot URL) is rendered
 * as a small footer rail so the chain stays auditable but doesn't
 * crowd the primary field block.
 */
export function TraceCard({ trace }: { trace: RawTrace }) {
  const meta = sourceMeta(trace.source);
  const avatarUrl =
    pickFirstString(trace.fields, ["avatar_url", "avatar", "profile_image_url", "image_url"]) ??
    null;
  const profileUrl =
    pickFirstString(trace.fields, ["profile_url", "url", "page_url", "html_url"]) ??
    null;

  // We surface every field to {@link FieldTable} *except* the ones we
  // already render specially in the card header (the avatar + the
  // profile URL above the table).
  const HIDDEN_KEYS = new Set([
    "avatar_url",
    "avatar",
    "profile_image_url",
    "image_url",
    "profile_url",
    "page_url",
    "html_url",
  ]);
  const tableRows = Object.entries(trace.fields)
    .filter(([k]) => !HIDDEN_KEYS.has(k))
    .map(([key, value]) => ({ key, value }));

  const shortHash = trace.evidence.payload_sha256.slice(0, 16);

  return (
    <article className="rounded-lg border border-ink-line bg-ink-panel/80 shadow-panel">
      <header className="flex flex-col gap-3 border-b border-ink-line bg-ink-subtle/40 p-4 sm:flex-row sm:items-start sm:gap-4">
        <Avatar src={avatarUrl} fallback={trace.identifier.value} alt={meta.label} />
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <h3 className="text-base font-semibold tracking-snug text-fg">
              {meta.label}
            </h3>
            <span className="rounded border border-accent/30 bg-accent-soft px-1.5 py-0.5 font-mono text-2xs uppercase tracking-[0.12em] text-accent">
              {fieldLabel(trace.identifier.type)}
            </span>
            <span className="font-mono text-xs text-fg [overflow-wrap:anywhere]">
              {trace.identifier.value}
            </span>
          </div>
          {profileUrl ? (
            <a
              href={profileUrl}
              target="_blank"
              rel="noreferrer"
              className="block font-mono text-xs text-accent underline-offset-2 hover:underline [overflow-wrap:anywhere]"
            >
              {profileUrl}
            </a>
          ) : null}
          {meta.tags.length > 0 ? (
            <div className="flex flex-wrap gap-1.5 pt-0.5">
              {meta.tags.map((tag) => (
                <span
                  key={tag}
                  className="rounded-full border border-ink-line bg-ink-subtle px-2 py-0.5 text-2xs font-medium uppercase tracking-[0.08em] text-fg-muted"
                >
                  {tag}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </header>

      <div className="space-y-3 p-4">
        <FieldTable rows={tableRows} emptyLabel="No additional fields." />
      </div>

      <footer className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-ink-line bg-ink-subtle/20 px-4 py-2 text-2xs text-fg-dim">
        <span className="font-mono uppercase tracking-[0.08em]" title={trace.evidence.payload_sha256}>
          sha {shortHash}…
        </span>
        <span aria-hidden>·</span>
        <span title={trace.evidence.fetched_at}>
          fetched {formatRelativeTime(trace.evidence.fetched_at)}
        </span>
        <a
          href={trace.evidence.source_url}
          target="_blank"
          rel="noreferrer"
          className="ml-auto font-mono text-accent underline-offset-2 hover:underline [overflow-wrap:anywhere]"
        >
          source
        </a>
        {trace.evidence.archive_url ? (
          <a
            href={trace.evidence.archive_url}
            target="_blank"
            rel="noreferrer"
            className="font-mono text-accent underline-offset-2 hover:underline"
          >
            archive
          </a>
        ) : null}
        {trace.evidence.screenshot_path ? (
          <a
            href={trace.evidence.screenshot_path}
            target="_blank"
            rel="noreferrer"
            className="font-mono text-accent underline-offset-2 hover:underline"
          >
            screenshot
          </a>
        ) : null}
      </footer>
    </article>
  );
}

function pickFirstString(
  source: Record<string, unknown>,
  keys: readonly string[],
): string | null {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "string" && value.trim().length > 0) {
      return value;
    }
  }
  return null;
}
