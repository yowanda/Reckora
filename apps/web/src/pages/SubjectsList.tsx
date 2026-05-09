import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, unwrap } from "@/api/client";
import type { SubjectSummary } from "@/api/types";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Spinner } from "@/components/Spinner";
import { formatRelativeTime, shortId } from "@/lib/format";

async function fetchSubjects(): Promise<SubjectSummary[]> {
  return unwrap(await api.GET("/api/v1/subjects"));
}

export function SubjectsListPage() {
  const query = useQuery({
    queryKey: ["subjects"],
    queryFn: fetchSubjects,
  });

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Subjects</h1>
          <p className="text-sm text-zinc-500">
            Dossiers visible to your account.
          </p>
        </div>
        <Link
          to="/investigations/new"
          className="rounded bg-accent-muted px-3 py-1.5 text-sm hover:bg-accent"
        >
          New investigation
        </Link>
      </div>
      {query.isPending ? <Spinner /> : null}
      {query.error ? <ErrorMessage error={query.error} /> : null}
      {query.data && query.data.length === 0 ? (
        <div className="rounded border border-border bg-bg-panel p-6 text-center text-sm text-zinc-400">
          No subjects yet. Start by running a new investigation.
        </div>
      ) : null}
      {query.data && query.data.length > 0 ? (
        <ul className="divide-y divide-border rounded border border-border bg-bg-panel">
          {query.data.map((subject) => (
            <SubjectRow key={subject.id} subject={subject} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function SubjectRow({ subject }: { subject: SubjectSummary }) {
  return (
    <li className="px-4 py-3 hover:bg-bg-subtle">
      <Link
        to={`/subjects/${subject.id}`}
        className="flex items-center justify-between gap-4"
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm">
            <span className="rounded bg-bg-subtle px-1.5 py-0.5 font-mono text-xs text-zinc-400">
              {subject.seed.kind}
            </span>
            <span className="truncate font-medium">{subject.seed.value}</span>
            {subject.owner_username ? (
              <span className="text-xs text-zinc-500">
                · {subject.owner_username}
              </span>
            ) : null}
          </div>
          <div className="mt-1 flex items-center gap-3 text-xs text-zinc-500">
            <span className="font-mono">{shortId(subject.id)}</span>
            <span>created {formatRelativeTime(subject.created_at)}</span>
            <span>{subject.identifier_count} ids</span>
            <span>{subject.trace_count} traces</span>
          </div>
        </div>
        <span className="shrink-0 text-xs text-zinc-500">→</span>
      </Link>
    </li>
  );
}
