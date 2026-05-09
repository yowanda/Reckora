export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={`animate-pulse rounded bg-zinc-800/60 ${className}`}
    />
  );
}

/**
 * A list of card-shaped skeletons for use in list pages while data loads.
 * Replaces the bare <Spinner> we used to show.
 */
export function SkeletonList({ count = 4 }: { count?: number }) {
  return (
    <ul aria-busy="true" className="space-y-2">
      {Array.from({ length: count }).map((_, idx) => (
        <li
          key={idx}
          className="rounded border border-border bg-bg-panel p-3"
        >
          <div className="flex items-center gap-2">
            <Skeleton className="h-5 w-12" />
            <Skeleton className="h-4 flex-1 max-w-xs" />
          </div>
          <div className="mt-2 flex items-center gap-2">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-3 w-32" />
          </div>
        </li>
      ))}
    </ul>
  );
}
