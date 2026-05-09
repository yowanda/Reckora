import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, unwrap } from "@/api/client";
import type { SavedDossierSummary } from "@/api/types";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Spinner } from "@/components/Spinner";
import { formatRelativeTime, shortId } from "@/lib/format";

async function fetchPins(): Promise<SavedDossierSummary[]> {
  return unwrap(await api.GET("/api/v1/me/pins"));
}

async function unpin(subjectId: string): Promise<void> {
  unwrap(
    await api.DELETE("/api/v1/me/pins/{subject_id}", {
      params: { path: { subject_id: subjectId } },
    }),
  );
}

export function PinsPage() {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: ["me", "pins"], queryFn: fetchPins });
  const remove = useMutation({
    mutationFn: unpin,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me", "pins"] }),
  });

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Pinned subjects</h1>
        <p className="text-sm text-zinc-500">
          Quick access to the dossiers you have starred.
        </p>
      </div>
      {query.isPending ? <Spinner /> : null}
      {query.error ? <ErrorMessage error={query.error} /> : null}
      {query.data && query.data.length === 0 ? (
        <div className="rounded border border-border bg-bg-panel p-6 text-center text-sm text-zinc-400">
          Nothing pinned yet.
        </div>
      ) : null}
      {query.data ? (
        <ul className="divide-y divide-border rounded border border-border bg-bg-panel">
          {query.data.map((subject) => (
            <li
              key={subject.id}
              className="flex items-center gap-3 px-4 py-3"
            >
              <div className="min-w-0 flex-1">
                <Link
                  to={`/subjects/${subject.id}`}
                  className="block hover:underline"
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
              </div>
              <button
                type="button"
                onClick={() => remove.mutate(subject.id)}
                className="text-xs text-zinc-400 hover:text-zinc-100"
              >
                Unpin
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
