import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, unwrap } from "@/api/client";
import type { CrossReferenceList } from "@/api/types";
import { Spinner } from "@/components/Spinner";
import { shortId } from "@/lib/format";

async function fetchXrefs(subjectId: string): Promise<CrossReferenceList> {
  return unwrap(
    await api.GET("/api/v1/subjects/{subject_id}/cross-references", {
      params: { path: { subject_id: subjectId } },
    }),
  );
}

export function CrossReferences({ subjectId }: { subjectId: string }) {
  const query = useQuery({
    queryKey: ["subjects", subjectId, "xrefs"],
    queryFn: () => fetchXrefs(subjectId),
  });

  return (
    <section className="rounded border border-ink-line bg-ink-panel">
      <header className="border-b border-ink-line px-3 py-2 text-xs uppercase tracking-wide text-fg-dim">
        Cross-references
      </header>
      <div className="p-3 text-sm">
        {query.isPending ? <Spinner /> : null}
        {query.data && query.data.items.length === 0 ? (
          <p className="text-xs text-fg-dim">
            No overlap with other dossiers.
          </p>
        ) : null}
        <ul className="space-y-2">
          {(query.data?.items ?? []).map((entry) => (
            <li key={entry.identifier.kind + ":" + entry.identifier.value}>
              <div className="text-xs text-fg-muted">
                <span className="font-mono">{entry.identifier.kind}</span>{" "}
                <span className="text-fg">{entry.identifier.value}</span>
              </div>
              <ul className="mt-1 space-y-1 pl-3 text-xs">
                {entry.subjects.map((match) => (
                  <li
                    key={match.id}
                    className="flex items-baseline gap-2"
                  >
                    <Link
                      to={`/subjects/${match.id}`}
                      className="text-accent hover:underline"
                    >
                      {match.seed.kind}:{match.seed.value}
                    </Link>
                    <span className="font-mono text-fg-dim">
                      {shortId(match.id)}
                    </span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
