import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/api/client";
import type { StatusEntry } from "@/api/types";
import { ErrorMessage } from "@/components/ErrorMessage";

async function fetchStatus(subjectId: string): Promise<StatusEntry> {
  return unwrap(
    await api.GET("/api/v1/subjects/{subject_id}/status", {
      params: { path: { subject_id: subjectId } },
    }),
  );
}

async function fetchCatalog(): Promise<Record<string, number>> {
  return unwrap(await api.GET("/api/v1/status"));
}

async function setStatus(args: {
  subjectId: string;
  status: string;
}): Promise<StatusEntry> {
  return unwrap(
    await api.PUT("/api/v1/subjects/{subject_id}/status", {
      params: { path: { subject_id: args.subjectId } },
      body: { status: args.status },
    }),
  );
}

export function StatusPicker({ subjectId }: { subjectId: string }) {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["subjects", subjectId, "status"],
    queryFn: () => fetchStatus(subjectId),
  });
  const catalog = useQuery({
    queryKey: ["status-catalog"],
    queryFn: fetchCatalog,
  });
  const mutate = useMutation({
    mutationFn: setStatus,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["subjects", subjectId, "status"] });
      qc.invalidateQueries({ queryKey: ["subjects", subjectId, "activity"] });
      qc.invalidateQueries({ queryKey: ["status-catalog"] });
    },
  });

  const current = status.data?.status ?? "";
  const options = Array.from(
    new Set([current, ...Object.keys(catalog.data ?? {})].filter(Boolean)),
  );

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs uppercase tracking-wide text-zinc-500">
        Status
      </span>
      <select
        value={current}
        disabled={status.isPending || mutate.isPending}
        onChange={(e) => mutate.mutate({ subjectId, status: e.target.value })}
        className="rounded border border-border bg-bg-subtle px-2 py-1 text-xs"
      >
        {options.length === 0 ? <option value="">(none)</option> : null}
        {options.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
      {mutate.error ? <ErrorMessage error={mutate.error} /> : null}
    </div>
  );
}
