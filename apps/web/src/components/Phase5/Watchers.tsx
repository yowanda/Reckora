import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/api/client";
import type { WatcherEntry } from "@/api/types";
import { useAuth } from "@/lib/auth";
import { describeError, useToast } from "@/lib/toast";

async function fetchWatchers(subjectId: string): Promise<WatcherEntry[]> {
  return unwrap(
    await api.GET("/api/v1/subjects/{subject_id}/watchers", {
      params: { path: { subject_id: subjectId } },
    }),
  );
}

async function watch(subjectId: string): Promise<unknown> {
  return unwrap(
    await api.PUT("/api/v1/subjects/{subject_id}/watchers/me", {
      params: { path: { subject_id: subjectId } },
    }),
  );
}

async function unwatch(subjectId: string): Promise<void> {
  unwrap(
    await api.DELETE("/api/v1/subjects/{subject_id}/watchers/me", {
      params: { path: { subject_id: subjectId } },
    }),
  );
}

export function WatchToggle({ subjectId }: { subjectId: string }) {
  const qc = useQueryClient();
  const { state } = useAuth();
  const me = state.status === "authenticated" ? state.user.username : null;

  const watchers = useQuery({
    queryKey: ["subjects", subjectId, "watchers"],
    queryFn: () => fetchWatchers(subjectId),
  });

  const watching = (watchers.data ?? []).some((w) => w.username === me);
  const count = watchers.data?.length ?? 0;

  const toast = useToast();
  const setWatching = useMutation({
    mutationFn: async (next: boolean) => {
      if (next) {
        await watch(subjectId);
      } else {
        await unwatch(subjectId);
      }
      return next;
    },
    onSuccess: (next) => {
      qc.invalidateQueries({ queryKey: ["subjects", subjectId, "watchers"] });
      toast.push("success", next ? "Watching" : "Stopped watching");
    },
    onError: (error) => toast.push("error", describeError(error)),
  });

  return (
    <button
      type="button"
      onClick={() => setWatching.mutate(!watching)}
      className={`rounded border px-2 py-1 text-xs ${
        watching
          ? "border-accent bg-accent-muted text-zinc-100"
          : "border-border bg-bg-subtle text-zinc-300 hover:text-zinc-100"
      }`}
    >
      {watching ? "Watching" : "Watch"}
      <span className="ml-1 text-zinc-400">· {count}</span>
    </button>
  );
}
