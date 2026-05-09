import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { ErrorMessage } from "@/components/ErrorMessage";
import { Spinner } from "@/components/Spinner";
import { useAuth } from "@/lib/auth";

interface LocationState {
  from?: { pathname?: string };
}

export function LoginPage() {
  const { login, state } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (state.status === "authenticated") {
    const target =
      (location.state as LocationState | null)?.from?.pathname ?? "/subjects";
    queueMicrotask(() => {
      navigate(target, { replace: true });
    });
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      const target =
        (location.state as LocationState | null)?.from?.pathname ??
        "/subjects";
      navigate(target, { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm space-y-4 rounded-lg border border-border bg-bg-panel p-6"
      >
        <div>
          <h1 className="text-lg font-semibold">Sign in to Reckora</h1>
          <p className="mt-1 text-xs text-zinc-500">
            Use the credentials provisioned via{" "}
            <code className="font-mono text-zinc-400">
              reckora-api create-user
            </code>
            .
          </p>
        </div>
        <label className="block text-sm">
          <span className="mb-1 block text-zinc-400">Username</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            autoFocus
            autoComplete="username"
            className="w-full rounded border border-border bg-bg-subtle px-2 py-1.5 outline-none focus:border-accent"
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-zinc-400">Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
            className="w-full rounded border border-border bg-bg-subtle px-2 py-1.5 outline-none focus:border-accent"
          />
        </label>
        {error !== null ? <ErrorMessage error={error} /> : null}
        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded bg-accent-muted px-3 py-2 text-sm font-medium text-zinc-100 hover:bg-accent disabled:opacity-50"
        >
          {submitting ? <Spinner label="Signing in…" /> : "Sign in"}
        </button>
      </form>
    </div>
  );
}
