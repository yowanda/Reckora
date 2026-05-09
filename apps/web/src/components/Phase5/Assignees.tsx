import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "@/api/client";
import type { AssigneeEntry } from "@/api/types";
import { describeError, useToast } from "@/lib/toast";

async function fetchAssignees(subjectId: string): Promise<AssigneeEntry[]> {
  return unwrap(
    await api.GET("/api/v1/subjects/{subject_id}/assignees", {
      params: { path: { subject_id: subjectId } },
    }),
  );
}

async function addAssignee(args: {
  subjectId: string;
  username: string;
}): Promise<AssigneeEntry> {
  return unwrap(
    await api.POST("/api/v1/subjects/{subject_id}/assignees", {
      params: { path: { subject_id: args.subjectId } },
      body: { username: args.username },
    }),
  );
}

async function removeAssignee(args: {
  subjectId: string;
  username: string;
}): Promise<void> {
  unwrap(
    await api.DELETE(
      "/api/v1/subjects/{subject_id}/assignees/{username}",
      {
        params: {
          path: { subject_id: args.subjectId, username: args.username },
        },
      },
    ),
  );
}

export function Assignees({ subjectId }: { subjectId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const list = useQuery({
    queryKey: ["subjects", subjectId, "assignees"],
    queryFn: () => fetchAssignees(subjectId),
  });
  const invalidate = () => {
    qc.invalidateQueries({
      queryKey: ["subjects", subjectId, "assignees"],
    });
    qc.invalidateQueries({ queryKey: ["subjects", subjectId, "activity"] });
  };
  const add = useMutation({
    mutationFn: addAssignee,
    onSuccess: (entry) => {
      invalidate();
      toast.push("success", `Assigned @${entry.username}`);
    },
    onError: (error) => toast.push("error", describeError(error)),
  });
  const remove = useMutation({
    mutationFn: removeAssignee,
    onSuccess: (_data, variables) => {
      invalidate();
      toast.push("success", `Removed @${variables.username}`);
    },
    onError: (error) => toast.push("error", describeError(error)),
  });

  const [draft, setDraft] = useState("");

  return (
    <section className="rounded border border-border bg-bg-panel">
      <header className="border-b border-border px-3 py-2 text-xs uppercase tracking-wide text-zinc-500">
        Assignees
      </header>
      <div className="space-y-2 p-3">
        {(list.data ?? []).length === 0 ? (
          <p className="text-xs text-zinc-500">Nobody is assigned yet.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {(list.data ?? []).map((a) => (
              <li key={a.user_id} className="flex items-center gap-2">
                <span>{a.username}</span>
                <button
                  type="button"
                  onClick={() =>
                    remove.mutate({ subjectId, username: a.username })
                  }
                  className="ml-auto text-xs text-zinc-500 hover:text-red-300"
                >
                  remove
                </button>
              </li>
            ))}
          </ul>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const u = draft.trim();
            if (u === "") return;
            add.mutate(
              { subjectId, username: u },
              { onSuccess: () => setDraft("") },
            );
          }}
          className="flex gap-2"
        >
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="username"
            className="flex-1 rounded border border-border bg-bg-subtle px-2 py-1 text-xs"
          />
          <button
            type="submit"
            disabled={add.isPending}
            className="rounded bg-accent-muted px-2 py-1 text-xs hover:bg-accent disabled:opacity-50"
          >
            Assign
          </button>
        </form>
      </div>
    </section>
  );
}
