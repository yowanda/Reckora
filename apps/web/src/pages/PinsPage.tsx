import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/api/client";
import type { SavedDossierSummary } from "@/api/types";
import { EmptyState } from "@/components/EmptyState";
import { ErrorMessage } from "@/components/ErrorMessage";
import { SavedDossierList } from "@/components/SavedDossierList";
import { SkeletonList } from "@/components/Skeleton";
import { describeError, useToast } from "@/lib/toast";

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
  const toast = useToast();
  const query = useQuery({ queryKey: ["me", "pins"], queryFn: fetchPins });
  const remove = useMutation({
    mutationFn: unpin,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["me", "pins"] });
      toast.push("success", "Unpinned");
    },
    onError: (error) => toast.push("error", describeError(error)),
  });

  return (
    <section className="space-y-5">
      <header>
        <div className="text-2xs font-medium uppercase tracking-[0.22em] text-fg-dim">
          My queue
        </div>
        <h1 className="mt-1 text-2xl font-semibold tracking-snug text-fg">
          Pinned
        </h1>
        <p className="mt-1 text-sm text-fg-muted">
          Quick access to the dossiers you have starred.
        </p>
      </header>

      {query.isPending ? <SkeletonList count={3} /> : null}
      {query.error ? <ErrorMessage error={query.error} /> : null}
      {query.data && query.data.length === 0 ? (
        <EmptyState
          icon="☆"
          title="Nothing pinned yet"
          description="Open a subject and click ‘Pin’ to add it here for quick access."
        />
      ) : null}
      {query.data && query.data.length > 0 ? (
        <SavedDossierList
          items={query.data}
          rowAction={(subject) => (
            <button
              type="button"
              onClick={() => remove.mutate(subject.id)}
              disabled={remove.isPending}
              className="rounded border border-ink-line bg-ink-subtle px-2 py-1 text-2xs uppercase tracking-[0.18em] text-fg-muted transition-colors hover:border-danger/50 hover:text-danger disabled:opacity-50"
            >
              Unpin
            </button>
          )}
        />
      ) : null}
    </section>
  );
}
