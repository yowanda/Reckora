import { useQuery } from "@tanstack/react-query";

import { api, unwrap } from "@/api/client";
import type { SavedDossierSummary } from "@/api/types";
import { EmptyState } from "@/components/EmptyState";
import { ErrorMessage } from "@/components/ErrorMessage";
import { SavedDossierList } from "@/components/SavedDossierList";
import { SkeletonList } from "@/components/Skeleton";

async function fetchWatching(): Promise<SavedDossierSummary[]> {
  return unwrap(await api.GET("/api/v1/me/watching"));
}

export function WatchingPage() {
  const query = useQuery({
    queryKey: ["me", "watching"],
    queryFn: fetchWatching,
  });

  return (
    <section className="space-y-5">
      <header>
        <div className="text-2xs font-medium uppercase tracking-[0.22em] text-fg-dim">
          My queue
        </div>
        <h1 className="mt-1 text-2xl font-semibold tracking-snug text-fg">
          Watching
        </h1>
        <p className="mt-1 text-sm text-fg-muted">
          Subjects whose comments and status changes you follow.
        </p>
      </header>

      {query.isPending ? <SkeletonList count={3} /> : null}
      {query.error ? <ErrorMessage error={query.error} /> : null}
      {query.data && query.data.length === 0 ? (
        <EmptyState
          icon="○"
          title="Not watching anything"
          description="Open a subject and click ‘Watch’ to follow new comments and status changes."
        />
      ) : null}
      {query.data && query.data.length > 0 ? (
        <SavedDossierList
          items={query.data}
          rowChip={() => (
            <span className="inline-flex items-center gap-1 rounded border border-ok/30 bg-ok-soft px-1.5 py-0.5 text-2xs uppercase tracking-[0.12em] text-ok">
              <span className="rk-live-dot inline-block h-1.5 w-1.5 rounded-full bg-ok" />
              live
            </span>
          )}
        />
      ) : null}
    </section>
  );
}
