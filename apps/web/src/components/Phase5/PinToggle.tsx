import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/api/client";
import type { SavedDossierSummary } from "@/api/types";

async function fetchPins(): Promise<SavedDossierSummary[]> {
  return unwrap(await api.GET("/api/v1/me/pins"));
}

async function pin(subjectId: string): Promise<unknown> {
  return unwrap(
    await api.POST("/api/v1/me/pins/{subject_id}", {
      params: { path: { subject_id: subjectId } },
    }),
  );
}

async function unpin(subjectId: string): Promise<void> {
  unwrap(
    await api.DELETE("/api/v1/me/pins/{subject_id}", {
      params: { path: { subject_id: subjectId } },
    }),
  );
}

export function PinToggle({ subjectId }: { subjectId: string }) {
  const qc = useQueryClient();
  const pins = useQuery({ queryKey: ["me", "pins"], queryFn: fetchPins });
  const pinned = (pins.data ?? []).some((s) => s.id === subjectId);
  const setPinned = useMutation({
    mutationFn: async (next: boolean) => {
      if (next) {
        await pin(subjectId);
      } else {
        await unpin(subjectId);
      }
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me", "pins"] }),
  });

  return (
    <button
      type="button"
      onClick={() => setPinned.mutate(!pinned)}
      className={`rounded border px-2 py-1 text-xs ${
        pinned
          ? "border-accent bg-accent-muted text-zinc-100"
          : "border-border bg-bg-subtle text-zinc-300 hover:text-zinc-100"
      }`}
    >
      {pinned ? "★ Pinned" : "☆ Pin"}
    </button>
  );
}
