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
    <section className="space-y-5">
      <header>
        <div className="text-2xs font-medium uppercase tracking-[0.22em] text-fg-dim">
          My queue
        </div>
        <h1 className="mt-1 text-2xl font-semibold tracking-snug text-fg">
          Mentions
        </h1>
        <p className="mt-1 text-sm text-fg-muted">
          Threads where teammates tagged you with{" "}
          <code className="font-mono text-fg">@username</code>.
        </p>
      </header>

      {query.isPending ? <SkeletonList count={3} /> : null}
      {query.error ? <ErrorMessage error={query.error} /> : null}
      {query.data && query.data.length === 0 ? (
        <EmptyState
          icon="@"
          title="No mentions yet"
          description="When teammates tag you with @username in a comment, the thread shows up here."
        />
      ) : null}
      {query.data && query.data.length > 0 ? (
        <ul className="divide-y divide-ink-line overflow-hidden rounded-lg border border-ink-line bg-ink-panel">
          {query.data.map((mention) => (
            <li key={mention.comment_id}>
              <Link
                to={`/subjects/${mention.subject_id}`}
                className="block px-4 py-3 transition-colors hover:bg-ink-subtle/60"
              >
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs text-fg-dim">
                  <span className="rounded border border-accent/30 bg-accent-soft px-1.5 py-0.5 font-mono uppercase tracking-[0.12em] text-accent">
                    @mention
                  </span>
                  <span className="font-mono uppercase tracking-[0.08em]">
                    {shortId(mention.subject_id)}
                  </span>
                  <span>·</span>
                  <span>
                    by{" "}
                    <span className="text-fg-muted">
                      {mention.author_username ??
                        `user ${mention.author_user_id}`}
                    </span>
                  </span>
                  <span>·</span>
                  <span>{formatRelativeTime(mention.comment_created_at)}</span>
                </div>
                <p className="mt-1.5 text-sm leading-relaxed text-fg">
                  {mention.body}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
