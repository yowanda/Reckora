export function Spinner({ label }: { label?: string }) {
  return (
    <div
      className="inline-flex items-center gap-2 text-sm text-fg-muted"
      role="status"
    >
      <span
        className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-[1.5px] border-ink-line2 border-t-accent"
        aria-hidden="true"
      />
      <span>{label ?? "Loading…"}</span>
    </div>
  );
}
