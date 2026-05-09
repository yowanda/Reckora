import { fieldLabel } from "./sources";

/**
 * Two-column "label / value" mini-table rendered inside every
 * {@link TraceCard}.
 *
 * The screenshot reference (Maigret) uses a `Username | LintangArsaNaura`
 * layout with the label in the left column and the value flowing into
 * the right column. We reproduce that with a CSS grid so the columns
 * align across all rows of a card without the cells fighting over flex
 * basis when the value contains a long URL or a multi-line bio.
 *
 * URL-shaped values get rendered as clickable anchors with
 * `[overflow-wrap:anywhere]` so they never push the card off-screen on
 * mobile — critical because the markdown renderer was wrapping URLs in
 * the middle of a slug instead of at a natural boundary.
 */
export function FieldTable({
  rows,
  emptyLabel,
}: {
  rows: Array<{ key: string; value: unknown }>;
  emptyLabel?: string;
}) {
  const filtered = rows.filter(({ value }) => isRenderable(value));
  if (filtered.length === 0) {
    return emptyLabel ? (
      <p className="text-2xs text-fg-dim">{emptyLabel}</p>
    ) : null;
  }
  return (
    <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-xs">
      {filtered.map(({ key, value }) => (
        <Row key={key} k={key} v={value} />
      ))}
    </dl>
  );
}

function Row({ k, v }: { k: string; v: unknown }) {
  return (
    <>
      <dt className="font-semibold text-fg-muted">{fieldLabel(k)}</dt>
      <dd className="min-w-0 text-fg [overflow-wrap:anywhere]">
        <FieldValue value={v} />
      </dd>
    </>
  );
}

function FieldValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <span className="text-fg-dim">—</span>;
  }
  if (typeof value === "string") {
    if (looksLikeUrl(value)) {
      return (
        <a
          href={value}
          target="_blank"
          rel="noreferrer"
          className="font-mono text-xs text-accent underline-offset-2 hover:underline"
        >
          {value}
        </a>
      );
    }
    return <span className="whitespace-pre-wrap">{value}</span>;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return <span className="font-mono">{String(value)}</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return <span className="text-fg-dim">—</span>;
    }
    return (
      <ul className="space-y-1">
        {value.map((item, i) => (
          <li key={i}>
            <FieldValue value={item} />
          </li>
        ))}
      </ul>
    );
  }
  // Object / unknown structured payload — render a compact JSON dump
  // (preferable to losing the data, but kept monospace + small).
  return (
    <code className="block whitespace-pre-wrap break-all rounded border border-ink-line bg-ink-subtle/50 px-2 py-1 font-mono text-2xs text-fg-muted">
      {JSON.stringify(value, null, 2)}
    </code>
  );
}

function isRenderable(value: unknown): boolean {
  if (value === null || value === undefined) {
    return false;
  }
  if (typeof value === "string" && value.length === 0) {
    return false;
  }
  if (Array.isArray(value) && value.length === 0) {
    return false;
  }
  return true;
}

function looksLikeUrl(s: string): boolean {
  return /^https?:\/\//i.test(s.trim());
}
