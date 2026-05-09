import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import ReactMarkdown from "react-markdown";
import { useNavigate, useParams } from "react-router-dom";

import { api, unwrap } from "@/api/client";
import type { SavedDossierPayload } from "@/api/types";
import { ErrorMessage } from "@/components/ErrorMessage";
import { ActivityFeed } from "@/components/Phase5/Activity";
import { Assignees } from "@/components/Phase5/Assignees";
import { Comments } from "@/components/Phase5/Comments";
import { LabelChips } from "@/components/Phase5/Labels";
import { PrivateNote } from "@/components/Phase5/Notes";
import { PinToggle } from "@/components/Phase5/PinToggle";
import { StatusPicker } from "@/components/Phase5/Status";
import { Todos } from "@/components/Phase5/Todos";
import { WatchToggle } from "@/components/Phase5/Watchers";
import { CrossReferences } from "@/components/Phase5/Xref";
import { Spinner } from "@/components/Spinner";
import { formatRelativeTime, shortId } from "@/lib/format";

async function fetchSubject(id: string): Promise<SavedDossierPayload> {
  return unwrap(
    await api.GET("/api/v1/subjects/{subject_id}", {
      params: { path: { subject_id: id } },
    }),
  );
}

async function fetchDossier(id: string): Promise<string> {
  const result = await api.GET("/api/v1/subjects/{subject_id}/dossier", {
    params: {
      path: { subject_id: id },
      query: { format: "md" },
    },
    parseAs: "text",
  });
  if (result.error !== undefined || result.data === undefined) {
    throw new Error(`Failed to fetch dossier (${result.response.status})`);
  }
  return result.data as unknown as string;
}

async function recordVisit(subjectId: string): Promise<void> {
  unwrap(
    await api.POST("/api/v1/subjects/{subject_id}/visits/me", {
      params: { path: { subject_id: subjectId } },
    }),
  );
}

async function deleteSubject(id: string): Promise<void> {
  unwrap(
    await api.DELETE("/api/v1/subjects/{subject_id}", {
      params: { path: { subject_id: id } },
    }),
  );
}

interface SeedSnippet {
  kind: string;
  value: string;
}

function extractSeed(payload: SavedDossierPayload): SeedSnippet | null {
  const subject = payload.subject;
  if (!subject || typeof subject !== "object") {
    return null;
  }
  const kind =
    (subject as Record<string, unknown>).kind ??
    (subject as Record<string, unknown>).type;
  const value = (subject as Record<string, unknown>).value;
  if (typeof kind !== "string" || typeof value !== "string") {
    return null;
  }
  return { kind, value };
}

export function SubjectDetailPage() {
  const params = useParams<{ subjectId: string }>();
  const subjectId = params.subjectId ?? "";
  const navigate = useNavigate();
  const qc = useQueryClient();

  const subject = useQuery({
    queryKey: ["subjects", subjectId, "summary"],
    queryFn: () => fetchSubject(subjectId),
    enabled: subjectId !== "",
  });
  const dossier = useQuery({
    queryKey: ["subjects", subjectId, "dossier", "md"],
    queryFn: () => fetchDossier(subjectId),
    enabled: subjectId !== "",
  });

  useEffect(() => {
    if (subjectId === "") {
      return;
    }
    void recordVisit(subjectId).catch(() => {
      // best-effort; ignore
    });
  }, [subjectId]);

  const remove = useMutation({
    mutationFn: deleteSubject,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["subjects"] });
      navigate("/subjects");
    },
  });

  if (subjectId === "") {
    return null;
  }

  const seed = subject.data ? extractSeed(subject.data) : null;

  return (
    <section className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="space-y-4">
        <header className="space-y-3 rounded border border-border bg-bg-panel p-4">
          {subject.isPending ? <Spinner /> : null}
          {subject.error ? <ErrorMessage error={subject.error} /> : null}
          {subject.data ? (
            <>
              <div className="flex flex-wrap items-baseline gap-2">
                <h1 className="text-lg font-semibold">
                  <span className="rounded bg-bg-subtle px-1.5 py-0.5 font-mono text-xs text-zinc-400">
                    {seed?.kind ?? "subject"}
                  </span>{" "}
                  <span>{seed?.value ?? "(unknown seed)"}</span>
                </h1>
                <span className="font-mono text-xs text-zinc-500">
                  {shortId(subject.data.id, 12)}
                </span>
                <span className="text-xs text-zinc-500">
                  · created {formatRelativeTime(subject.data.created_at)}
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusPicker subjectId={subjectId} />
                <WatchToggle subjectId={subjectId} />
                <PinToggle subjectId={subjectId} />
                <button
                  type="button"
                  onClick={() => {
                    if (
                      confirm("Delete this subject? This cannot be undone.")
                    ) {
                      remove.mutate(subjectId);
                    }
                  }}
                  className="ml-auto rounded border border-red-900/60 bg-red-950/30 px-2 py-1 text-xs text-red-200 hover:bg-red-900/40"
                >
                  Delete
                </button>
              </div>
              <LabelChips subjectId={subjectId} />
            </>
          ) : null}
        </header>

        <article className="rounded border border-border bg-bg-panel p-4">
          {dossier.isPending ? <Spinner label="Rendering dossier…" /> : null}
          {dossier.error ? <ErrorMessage error={dossier.error} /> : null}
          {dossier.data ? (
            <div className="prose prose-invert prose-sm max-w-none">
              <ReactMarkdown>{dossier.data}</ReactMarkdown>
            </div>
          ) : null}
        </article>

        <Comments subjectId={subjectId} />
      </div>
      <aside className="space-y-4">
        <ActivityFeed subjectId={subjectId} />
        <Assignees subjectId={subjectId} />
        <PrivateNote subjectId={subjectId} />
        <Todos subjectId={subjectId} />
        <CrossReferences subjectId={subjectId} />
      </aside>
    </section>
  );
}
