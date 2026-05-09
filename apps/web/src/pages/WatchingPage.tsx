import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, unwrap } from "@/api/client";
import type { SavedDossierSummary } from "@/api/types";
import { EmptyState } from "@/components/EmptyState";
import { ErrorMessage } from "@/components/ErrorMessage";
import { SkeletonList } from "@/components/Skeleton";
import { formatRelativeTime, shortId } from "@/lib/format";

async function fetchWatching(): Promise<SavedDossierSummary[]> {
  return unwrap(await api.GET("/api/v1/me/watching"));
}

export function WatchingPage() {
  const query = useQuery({
    queryKey: ["me", "watching"],
    queryFn: fetchWatching,
  });

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Watching</h1>
        <p className="text-sm text-zinc-500">
          Subjects whose activity you follow.
        </p>
      </div>
      {query.isPending ? <SkeletonList count={3} /> : null}
      {query.error ? <ErrorMessage error={query.error} /> : null}
      {query.data && query.data.length === 0 ? (
        <EmptyState
          icon="○"
          title="Not watching anything"
          description="Open a subject and click ‘Watch’ to follow new comments and status changes."
        />
      ) : null}
      {query.data ? (
        <ul className="divide-y divide-border rounded border border-border bg-bg-panel">
          {query.data.map((subject) => (
            <li key={subject.id} className="px-4 py-3">
              <Link
                to={`/subjects/${subject.id}`}
                className="block hover:bg-bg-subtle"
              >
                <div className="flex items-center gap-2 text-sm">
                  <span className="rounded bg-bg-subtle px-1.5 py-0.5 font-mono text-xs text-zinc-400">
                    {subject.seed_identifier.type}
                  </span>
                  <span className="truncate font-medium">
                    {subject.seed_identifier.value}
                  </span>
                </div>
                <div className="mt-1 text-xs text-zinc-500">
                  <span className="font-mono">{shortId(subject.id)}</span>
                  <span className="mx-1">·</span>
                  <span>created {formatRelativeTime(subject.created_at)}</span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
