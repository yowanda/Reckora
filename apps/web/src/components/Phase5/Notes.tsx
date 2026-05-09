import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, ApiError, unwrap } from "@/api/client";
import type { NoteEntry } from "@/api/types";
import { ErrorMessage } from "@/components/ErrorMessage";

async function fetchNote(subjectId: string): Promise<NoteEntry | null> {
  const result = await api.GET("/api/v1/subjects/{subject_id}/notes/me", {
    params: { path: { subject_id: subjectId } },
  });
  if (result.response.status === 404) {
    return null;
  }
  return unwrap(result);
}

async function saveNote(args: {
  subjectId: string;
  body: string;
}): Promise<NoteEntry> {
  return unwrap(
    await api.PUT("/api/v1/subjects/{subject_id}/notes/me", {
      params: { path: { subject_id: args.subjectId } },
      body: { body: args.body },
    }),
  );
}

async function clearNote(subjectId: string): Promise<void> {
  unwrap(
    await api.DELETE("/api/v1/subjects/{subject_id}/notes/me", {
      params: { path: { subject_id: subjectId } },
    }),
  );
}

export function PrivateNote({ subjectId }: { subjectId: string }) {
  const qc = useQueryClient();
  const note = useQuery({
    queryKey: ["subjects", subjectId, "notes", "me"],
    queryFn: () => fetchNote(subjectId),
    retry: (count, err) =>
      !(err instanceof ApiError && err.status === 404) && count < 1,
  });
  const save = useMutation({
    mutationFn: saveNote,
    onSuccess: () =>
      qc.invalidateQueries({
        queryKey: ["subjects", subjectId, "notes", "me"],
      }),
  });
  const clear = useMutation({
    mutationFn: clearNote,
    onSuccess: () =>
      qc.invalidateQueries({
        queryKey: ["subjects", subjectId, "notes", "me"],
      }),
  });

  const [draft, setDraft] = useState("");
  useEffect(() => {
    setDraft(note.data?.body ?? "");
  }, [note.data?.body]);

  return (
    <section className="rounded border border-border bg-bg-panel">
      <header className="border-b border-border px-3 py-2 text-xs uppercase tracking-wide text-zinc-500">
        Private note
      </header>
      <div className="space-y-2 p-3">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={4}
          placeholder="Only visible to you."
          className="w-full rounded border border-border bg-bg-subtle px-2 py-1 text-sm outline-none focus:border-accent"
        />
        {save.error ? <ErrorMessage error={save.error} /> : null}
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => save.mutate({ subjectId, body: draft })}
            disabled={save.isPending}
            className="rounded bg-accent-muted px-2 py-1 text-xs hover:bg-accent disabled:opacity-50"
          >
            Save
          </button>
          {note.data ? (
            <button
              type="button"
              onClick={() => clear.mutate(subjectId)}
              className="rounded border border-border bg-bg-subtle px-2 py-1 text-xs"
            >
              Clear
            </button>
          ) : null}
        </div>
      </div>
    </section>
  );
}
