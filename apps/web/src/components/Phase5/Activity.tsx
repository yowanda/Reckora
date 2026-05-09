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
    <div className="overflow-hidden rounded-lg border border-ink-line bg-ink-panel">
      <header className="border-b border-ink-line bg-ink-subtle/40 px-3 py-2 text-2xs font-medium uppercase tracking-[0.2em] text-fg-dim">
        Activity
      </header>
      <div className="p-3">
        {query.isPending ? <Spinner /> : null}
        {query.error ? <ErrorMessage error={query.error} /> : null}
        {query.data && query.data.length === 0 ? (
          <p className="text-2xs text-fg-dim">No activity yet.</p>
        ) : null}
        <ol className="space-y-1.5 text-xs">
          {(query.data ?? []).map((event, i) => (
            <li key={i} className="flex items-baseline gap-2 leading-relaxed">
              <span className="font-medium text-fg">
                {event.actor_username ?? "system"}
              </span>
              <span className="text-fg-muted">{KIND_LABEL[event.kind]}</span>
              {event.target_username ? (
                <span className="font-medium text-fg">{event.target_username}</span>
              ) : null}
              <span className="ml-auto shrink-0 text-2xs text-fg-dim">
                {formatRelativeTime(event.created_at)}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
