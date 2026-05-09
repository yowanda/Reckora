import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "@/api/client";
import type { TodoEntry } from "@/api/types";
import { describeError, useToast } from "@/lib/toast";

async function fetchTodos(subjectId: string): Promise<TodoEntry[]> {
  return unwrap(
    await api.GET("/api/v1/subjects/{subject_id}/todos/me", {
      params: { path: { subject_id: subjectId } },
    }),
  );
}

async function createTodo(args: {
  subjectId: string;
  body: string;
}): Promise<TodoEntry> {
  return unwrap(
    await api.POST("/api/v1/subjects/{subject_id}/todos/me", {
      params: { path: { subject_id: args.subjectId } },
      body: { body: args.body },
    }),
  );
}

async function patchTodo(args: {
  subjectId: string;
  todoId: number;
  body?: string;
  done?: boolean;
}): Promise<TodoEntry> {
  return unwrap(
    await api.PATCH("/api/v1/subjects/{subject_id}/todos/me/{todo_id}", {
      params: {
        path: { subject_id: args.subjectId, todo_id: args.todoId },
      },
      body: { body: args.body, done: args.done },
    }),
  );
}

async function deleteTodo(args: {
  subjectId: string;
  todoId: number;
}): Promise<void> {
  unwrap(
    await api.DELETE(
      "/api/v1/subjects/{subject_id}/todos/me/{todo_id}",
      {
        params: {
          path: { subject_id: args.subjectId, todo_id: args.todoId },
        },
      },
    ),
  );
}

export function Todos({ subjectId }: { subjectId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const list = useQuery({
    queryKey: ["subjects", subjectId, "todos", "me"],
    queryFn: () => fetchTodos(subjectId),
  });
  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ["subjects", subjectId, "todos", "me"] });

  const create = useMutation({
    mutationFn: createTodo,
    onSuccess: () => {
      invalidate();
      toast.push("success", "Task added");
    },
    onError: (error) => toast.push("error", describeError(error)),
  });
  const patch = useMutation({
    mutationFn: patchTodo,
    onSuccess: invalidate,
    onError: (error) => toast.push("error", describeError(error)),
  });
  const remove = useMutation({
    mutationFn: deleteTodo,
    onSuccess: () => {
      invalidate();
      toast.push("success", "Task removed");
    },
    onError: (error) => toast.push("error", describeError(error)),
  });

  const [draft, setDraft] = useState("");

  return (
    <section className="rounded border border-border bg-bg-panel">
      <header className="border-b border-border px-3 py-2 text-xs uppercase tracking-wide text-zinc-500">
        Todo
      </header>
      <div className="space-y-2 p-3">
        <ul className="space-y-1 text-sm">
          {(list.data ?? []).map((todo) => (
            <li key={todo.id} className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={todo.done}
                onChange={(e) =>
                  patch.mutate({
                    subjectId,
                    todoId: todo.id,
                    done: e.target.checked,
                  })
                }
              />
              <span className={todo.done ? "line-through text-zinc-500" : ""}>
                {todo.body}
              </span>
              <button
                type="button"
                onClick={() =>
                  remove.mutate({ subjectId, todoId: todo.id })
                }
                className="ml-auto text-xs text-zinc-500 hover:text-red-300"
              >
                ×
              </button>
            </li>
          ))}
          {(list.data ?? []).length === 0 ? (
            <li className="text-xs text-zinc-500">No tasks.</li>
          ) : null}
        </ul>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const value = draft.trim();
            if (value === "") return;
            create.mutate(
              { subjectId, body: value },
              { onSuccess: () => setDraft("") },
            );
          }}
          className="flex gap-2"
        >
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="add task"
            className="flex-1 rounded border border-border bg-bg-subtle px-2 py-1 text-xs"
          />
          <button
            type="submit"
            disabled={create.isPending}
            className="rounded bg-accent-muted px-2 py-1 text-xs hover:bg-accent disabled:opacity-50"
          >
            Add
          </button>
        </form>
      </div>
    </section>
  );
}
