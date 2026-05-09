import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "@/api/client";
import type { LabelEntry } from "@/api/types";
import { describeError, useToast } from "@/lib/toast";

async function fetchLabels(subjectId: string): Promise<LabelEntry[]> {
  return unwrap(
    await api.GET("/api/v1/subjects/{subject_id}/labels", {
      params: { path: { subject_id: subjectId } },
    }),
  );
}

async function addLabel(args: {
  subjectId: string;
  label: string;
}): Promise<LabelEntry[]> {
  return unwrap(
    await api.PUT("/api/v1/subjects/{subject_id}/labels/{label}", {
      params: { path: { subject_id: args.subjectId, label: args.label } },
    }),
  );
}

async function removeLabel(args: {
  subjectId: string;
  label: string;
}): Promise<void> {
  unwrap(
    await api.DELETE("/api/v1/subjects/{subject_id}/labels/{label}", {
      params: { path: { subject_id: args.subjectId, label: args.label } },
    }),
  );
}

export function LabelChips({ subjectId }: { subjectId: string }) {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["subjects", subjectId, "labels"],
    queryFn: () => fetchLabels(subjectId),
  });
  const toast = useToast();
  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ["subjects", subjectId, "labels"] });
  const add = useMutation({
    mutationFn: addLabel,
    onSuccess: (_data, variables) => {
      invalidate();
      toast.push("success", `Added label \u201c${variables.label}\u201d`);
    },
    onError: (error) => toast.push("error", describeError(error)),
  });
  const remove = useMutation({
    mutationFn: removeLabel,
    onSuccess: (_data, variables) => {
      invalidate();
      toast.push("success", `Removed label \u201c${variables.label}\u201d`);
    },
    onError: (error) => toast.push("error", describeError(error)),
  });

  const [draft, setDraft] = useState("");

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs uppercase tracking-wide text-zinc-500">
        Labels
      </span>
      {(list.data ?? []).map((label) => (
        <span
          key={label.label}
          className="inline-flex items-center gap-1 rounded-full border border-border bg-bg-subtle px-2 py-0.5 text-xs"
        >
          {label.label}
          <button
            type="button"
            aria-label={`Remove label ${label.label}`}
            onClick={() => remove.mutate({ subjectId, label: label.label })}
            className="text-zinc-500 hover:text-zinc-100"
          >
            ×
          </button>
        </span>
      ))}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const value = draft.trim();
          if (value === "") return;
          add.mutate({ subjectId, label: value });
          setDraft("");
        }}
        className="inline-flex items-center gap-1"
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="add label"
          className="w-28 rounded border border-border bg-bg-subtle px-2 py-0.5 text-xs"
        />
      </form>
    </div>
  );
}
