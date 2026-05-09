/**
 * Generic shimmer block. Used standalone for one-off placeholders and
 * composed by `SkeletonList` for the standard list-page loading state.
 *
 * The animation lives in `index.css` (`@keyframes rk-shimmer`) and is
 * driven by a moving accent gradient over the ink-subtle base, so the
 * whole skeleton matches the forensic palette instead of clashing with
 * the bright Tailwind default `animate-pulse`.
 */
export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={`rk-skeleton rounded ${className}`}
    />
  );
}

/**
 * Card-shaped skeleton row repeated `count` times. Used by every list
 * page while React Query resolves so the page never collapses to a
 * blank box.
 */
export function SkeletonList({ count = 4 }: { count?: number }) {
  return (
    <ul
      aria-busy="true"
      aria-live="polite"
      className="space-y-2 motion-safe:animate-fade-in"
    >
      {Array.from({ length: count }).map((_, idx) => (
        <li
          key={idx}
          className="rounded-lg border border-ink-line bg-ink-panel p-3"
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
