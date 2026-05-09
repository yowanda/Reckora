import type { ReactNode } from "react";

export function EmptyState({
  icon = "·",
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="motion-safe:animate-fade-in relative overflow-hidden rounded-lg border border-dashed border-ink-line bg-ink-panel/50 px-6 py-10 text-center">
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 mx-auto h-px w-2/3 bg-gradient-to-r from-transparent via-accent/30 to-transparent"
      />
      <div
        aria-hidden="true"
        className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-full border border-ink-line bg-ink-subtle text-lg text-fg-muted"
      >
        {icon}
      </div>
      <p className="text-sm font-medium text-fg">{title}</p>
      {description ? (
        <p className="mx-auto mt-1.5 max-w-sm text-xs leading-relaxed text-fg-dim">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-4 flex justify-center">{action}</div> : null}
    </div>
  );
}
