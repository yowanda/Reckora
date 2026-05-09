import { useQuery } from "@tanstack/react-query";

import { api, unwrap } from "@/api/client";
import type { ActivityEvent } from "@/api/types";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Spinner } from "@/components/Spinner";
import { formatRelativeTime } from "@/lib/format";

async function fetchActivity(subjectId: string): Promise<ActivityEvent[]> {
  return unwrap(
    await api.GET("/api/v1/subjects/{subject_id}/activity", {
      params: { path: { subject_id: subjectId } },
    }),
  );
}

const KIND_LABEL: Record<ActivityEvent["kind"], string> = {
  comment_added: "commented",
  assigned: "assigned",
  shared: "shared",
  anchored: "anchored",
};

export function ActivityFeed({ subjectId }: { subjectId: string }) {
  const query = useQuery({
    queryKey: ["subjects", subjectId, "activity"],
    queryFn: () => fetchActivity(subjectId),
  });

  return (
    <div className="rounded border border-border bg-bg-panel">
      <header className="border-b border-border px-3 py-2 text-xs uppercase tracking-wide text-zinc-500">
        Activity
      </header>
      <div className="p-3">
        {query.isPending ? <Spinner /> : null}
        {query.error ? <ErrorMessage error={query.error} /> : null}
        {query.data && query.data.length === 0 ? (
          <p className="text-xs text-zinc-500">No activity yet.</p>
        ) : null}
        <ol className="space-y-2 text-xs">
          {(query.data ?? []).map((event, i) => (
            <li key={i} className="flex items-baseline gap-2">
              <span className="text-zinc-300">
                {event.actor_username ?? "system"}
              </span>
              <span className="text-zinc-500">{KIND_LABEL[event.kind]}</span>
              {event.target_username ? (
                <span className="text-zinc-300">{event.target_username}</span>
              ) : null}
              <span className="ml-auto shrink-0 text-zinc-500">
                {formatRelativeTime(event.created_at)}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
