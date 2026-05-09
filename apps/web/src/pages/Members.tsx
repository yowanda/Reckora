import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";

import { ApiError, api, unwrap } from "@/api/client";
import type { UserPublic } from "@/api/types";
import { EmptyState } from "@/components/EmptyState";
import { ErrorMessage } from "@/components/ErrorMessage";
import { SkeletonList } from "@/components/Skeleton";
import { Spinner } from "@/components/Spinner";
import { useAuth } from "@/lib/auth";
import { formatRelativeTime } from "@/lib/format";
import { describeError, useToast } from "@/lib/toast";

type Role = "admin" | "viewer";

async function fetchUsers(): Promise<UserPublic[]> {
  return unwrap(await api.GET("/api/v1/users"));
}

async function createMember(input: {
  username: string;
  password: string;
  role: Role;
}): Promise<UserPublic> {
  return unwrap(
    await api.POST("/api/v1/users", {
      body: { username: input.username, password: input.password, role: input.role },
    }),
  );
}

async function setUserRole(input: { userId: number; role: Role }): Promise<UserPublic> {
  return unwrap(
    await api.PATCH("/api/v1/users/{user_id}/role", {
      params: { path: { user_id: input.userId } },
      body: { role: input.role },
    }),
  );
}

export function MembersPage() {
  const { state } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();

  const usersQuery = useQuery({
    queryKey: ["users"],
    queryFn: fetchUsers,
    enabled: state.status === "authenticated" && state.user.role === "admin",
  });

  const createMut = useMutation({
    mutationFn: createMember,
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["users"] });
      toast.push("success", `Member "${created.username}" created`);
      setUsername("");
      setPassword("");
      setRole("viewer");
      setFormError(null);
    },
    onError: (err) => {
      const message =
        err instanceof ApiError && err.status === 409
          ? "Username already taken"
          : describeError(err);
      setFormError(message);
    },
  });

  const promoteMut = useMutation({
    mutationFn: setUserRole,
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: ["users"] });
      toast.push("success", `${updated.username} is now ${updated.role}`);
    },
    onError: (err) => toast.push("error", describeError(err)),
  });

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [formError, setFormError] = useState<string | null>(null);

  if (state.status !== "authenticated") {
    return null;
  }
  if (state.user.role !== "admin") {
    return <Navigate to="/subjects" replace />;
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    if (username.trim().length < 3) {
      setFormError("Username must be at least 3 characters.");
      return;
    }
    if (password.length < 8) {
      setFormError("Password must be at least 8 characters.");
      return;
    }
    if (!/^[A-Za-z0-9_-]+$/.test(username)) {
      setFormError("Username may only use letters, numbers, _ or -.");
      return;
    }
    createMut.mutate({ username: username.trim(), password, role });
  }

  return (
    <section className="space-y-6">
      <header>
        <div className="text-2xs font-medium uppercase tracking-[0.22em] text-fg-dim">
          Admin
        </div>
        <h1 className="mt-1 text-2xl font-semibold tracking-snug text-fg">
          Members
        </h1>
        <p className="mt-1 text-sm text-fg-muted">
          Operators and investigators who can sign in to this Reckora instance.
        </p>
      </header>

      <div className="grid gap-6 md:grid-cols-[1fr_320px]">
        <div className="space-y-3">
          <h2 className="text-2xs font-medium uppercase tracking-[0.18em] text-fg-dim">
            Existing accounts
          </h2>
          {usersQuery.isPending ? <SkeletonList count={3} /> : null}
          {usersQuery.error ? <ErrorMessage error={usersQuery.error} /> : null}
          {usersQuery.data && usersQuery.data.length === 0 ? (
            <EmptyState
              icon="·"
              title="No accounts yet"
              description="Use the form on the right to add the first member."
            />
          ) : null}
          {usersQuery.data ? (
            <ul className="divide-y divide-ink-line overflow-hidden rounded-lg border border-ink-line bg-ink-panel">
              {usersQuery.data.map((u) => {
                const isSelf = u.id === state.user.id;
                const isAdmin = u.role === "admin";
                return (
                  <li
                    key={u.id}
                    className="flex flex-wrap items-center gap-3 px-4 py-3"
                  >
                    <span
                      className={[
                        "mt-1 inline-block h-2 w-2 shrink-0 rounded-full",
                        isAdmin ? "bg-alert" : "bg-ok",
                      ].join(" ")}
                      aria-hidden
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2 text-sm">
                        <span className="truncate font-medium text-fg">
                          {u.username}
                        </span>
                        <span
                          className={`rounded border px-1.5 py-0.5 font-mono text-2xs uppercase tracking-[0.12em] ${
                            isAdmin
                              ? "border-alert/30 bg-alert-soft text-alert"
                              : "border-ink-line bg-ink-subtle text-fg-muted"
                          }`}
                        >
                          {u.role}
                        </span>
                        {isSelf ? (
                          <span className="rk-kbd">you</span>
                        ) : null}
                      </div>
                      <div className="mt-1 text-2xs text-fg-dim">
                        joined {formatRelativeTime(u.created_at)}
                      </div>
                    </div>
                    {isSelf ? null : (
                      <button
                        type="button"
                        onClick={() =>
                          promoteMut.mutate({
                            userId: u.id,
                            role: isAdmin ? "viewer" : "admin",
                          })
                        }
                        disabled={promoteMut.isPending}
                        className="rounded border border-ink-line bg-ink-subtle px-2 py-1 text-2xs uppercase tracking-[0.16em] text-fg-muted transition-colors hover:border-accent/40 hover:text-fg disabled:opacity-50"
                      >
                        {isAdmin ? "Demote" : "Promote"}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>

        <form
          onSubmit={onSubmit}
          className="overflow-hidden rounded-lg border border-ink-line bg-ink-panel"
        >
          <div className="border-b border-ink-line bg-ink-subtle/40 px-4 py-2 text-2xs font-medium uppercase tracking-[0.2em] text-fg-dim">
            Add a member
          </div>
          <div className="space-y-3 p-4">
            <p className="text-2xs text-fg-dim">
              Provisioned accounts can sign in immediately. Members default to
              viewer role.
            </p>
            <label className="block">
              <span className="mb-1 block text-2xs font-medium uppercase tracking-[0.18em] text-fg-dim">
                Username
              </span>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                autoComplete="off"
                minLength={3}
                maxLength={64}
                pattern="[A-Za-z0-9_-]+"
                className="w-full rounded border border-ink-line bg-ink/40 px-2.5 py-1.5 text-sm font-mono text-fg outline-none transition-colors focus:border-accent focus:shadow-ring"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-2xs font-medium uppercase tracking-[0.18em] text-fg-dim">
                Initial password
              </span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
                autoComplete="new-password"
                className="w-full rounded border border-ink-line bg-ink/40 px-2.5 py-1.5 text-sm font-mono text-fg outline-none transition-colors focus:border-accent focus:shadow-ring"
              />
              <span className="mt-1 block text-2xs text-fg-dim">
                At least 8 characters. Share with the member out-of-band.
              </span>
            </label>
            <label className="block">
              <span className="mb-1 block text-2xs font-medium uppercase tracking-[0.18em] text-fg-dim">
                Role
              </span>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as Role)}
                className="w-full rounded border border-ink-line bg-ink/40 px-2.5 py-1.5 text-sm font-mono text-fg outline-none transition-colors focus:border-accent focus:shadow-ring"
              >
                <option value="viewer">Viewer (member)</option>
                <option value="admin">Admin (full access)</option>
              </select>
            </label>
            {formError ? (
              <p className="rounded border border-danger/40 bg-danger-soft px-2 py-1.5 text-xs text-danger">
                {formError}
              </p>
            ) : null}
            <button
              type="submit"
              disabled={createMut.isPending}
              className="w-full rounded border border-accent/40 bg-accent-muted px-3 py-2 text-sm font-medium text-fg transition-colors hover:border-accent hover:bg-accent/30 disabled:opacity-50"
            >
              {createMut.isPending ? <Spinner label="Creating…" /> : "Add member"}
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}
