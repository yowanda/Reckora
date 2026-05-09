import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, unwrap } from "@/api/client";
import type { MentionEntry } from "@/api/types";
import { EmptyState } from "@/components/EmptyState";
import { ErrorMessage } from "@/components/ErrorMessage";
import { SkeletonList } from "@/components/Skeleton";
import { formatRelativeTime, shortId } from "@/lib/format";

async function fetchMentions(): Promise<MentionEntry[]> {
  return unwrap(await api.GET("/api/v1/me/mentions"));
}

export function MentionsPage() {
  const query = useQuery({
    queryKey: ["me", "mentions"],
    queryFn: fetchMentions,
  });

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Mentions</h1>
        <p className="text-sm text-zinc-500">
          Comments where you were tagged with <code>@username</code>.
        </p>
      </div>
      {query.isPending ? <SkeletonList count={3} /> : null}
      {query.error ? <ErrorMessage error={query.error} /> : null}
      {query.data && query.data.length === 0 ? (
        <EmptyState
          icon="@"
          title="No mentions yet"
          description="When teammates tag you with @username in a comment, the thread shows up here."
        />
      ) : null}
      {query.data ? (
        <ul className="divide-y divide-border rounded border border-border bg-bg-panel">
          {query.data.map((mention) => (
            <li key={mention.comment_id} className="px-4 py-3">
              <Link
                to={`/subjects/${mention.subject_id}`}
                className="block hover:bg-bg-subtle"
              >
                <div className="text-xs text-zinc-500">
                  <span className="font-mono">
                    {shortId(mention.subject_id)}
                  </span>
                  <span className="mx-1">·</span>
                  <span>
                    by{" "}
                    {mention.author_username ??
                      `user ${mention.author_user_id}`}
                  </span>
                  <span className="mx-1">·</span>
                  <span>{formatRelativeTime(mention.comment_created_at)}</span>
                </div>
                <p className="mt-1 text-sm">{mention.body}</p>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
