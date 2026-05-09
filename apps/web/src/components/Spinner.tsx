export function Spinner({ label }: { label?: string }) {
  return (
    <div
      className="inline-flex items-center gap-2 text-zinc-400 text-sm"
      role="status"
    >
      <span
        className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-zinc-500 border-t-transparent"
        aria-hidden="true"
      />
      {label ?? "Loading…"}
    </div>
  );
}
