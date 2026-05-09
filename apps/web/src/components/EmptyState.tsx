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
    <div className="rounded border border-dashed border-border bg-bg-panel/60 px-4 py-8 text-center">
      <div
        aria-hidden="true"
        className="mx-auto mb-2 flex h-9 w-9 items-center justify-center rounded-full bg-bg-subtle text-lg text-zinc-500"
      >
        {icon}
      </div>
      <p className="text-sm font-medium text-zinc-200">{title}</p>
      {description ? (
        <p className="mt-1 text-xs text-zinc-500">{description}</p>
      ) : null}
      {action ? <div className="mt-3 flex justify-center">{action}</div> : null}
    </div>
  );
}
