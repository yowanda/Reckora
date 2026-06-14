import { useEffect, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { api, unwrap } from "@/api/client";
import { LogoMark } from "@/components/LogoMark";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Spinner } from "@/components/Spinner";
import { useAuth } from "@/lib/auth";

const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "";

function buildOAuthUrl(provider: "github", nextPath: string): string {
  const params = new URLSearchParams({ next: nextPath });
  return `${API_BASE_URL}/api/v1/auth/oauth/${provider}/login?${params.toString()}`;
}

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
  const [githubEnabled, setGithubEnabled] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const providers = unwrap(
          await api.GET("/api/v1/auth/oauth/providers"),
        );
        if (!cancelled) {
          setGithubEnabled(providers.github);
        }
      } catch {
        // No OAuth providers configured — fall back to password-only.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const nextPath =
    (location.state as LocationState | null)?.from?.pathname ?? "/subjects";

  if (state.status === "authenticated") {
    queueMicrotask(() => {
      navigate(nextPath, { replace: true });
    });
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      navigate(nextPath, { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center px-4 py-10">
      {/* Ambient glow stripes — quiet, not noisy. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 overflow-hidden"
      >
        <div className="absolute -top-32 left-1/2 h-[480px] w-[720px] -translate-x-1/2 rounded-full bg-accent/10 blur-3xl" />
        <div className="absolute -bottom-40 left-1/4 h-[360px] w-[480px] rounded-full bg-alert/5 blur-3xl" />
      </div>

      <div className="relative w-full max-w-md">
        <div className="mb-6 flex items-center justify-center gap-3">
          <LogoMark className="h-9 w-9" />
          <div className="leading-tight">
            <div className="text-lg font-semibold tracking-snug text-fg">
              Reckora
            </div>
            <div className="text-2xs uppercase tracking-[0.3em] text-fg-dim">
              forensic OSINT investigation
            </div>
          </div>
        </div>

        <form
          onSubmit={onSubmit}
          className="space-y-4 rounded-lg border border-ink-line bg-ink-panel/90 p-6 shadow-panel backdrop-blur"
        >
          <div className="flex items-center justify-between border-b border-ink-line pb-3">
            <div>
              <h1 className="text-base font-semibold text-fg">Sign in</h1>
              <p className="mt-0.5 text-xs text-fg-muted">
                Authenticated session required.
              </p>
            </div>
            <span className="rk-kbd">v0.1.0</span>
          </div>

          <label className="block">
            <span className="mb-1 block text-2xs font-medium uppercase tracking-[0.18em] text-fg-dim">
              Operator
            </span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              autoFocus
              autoComplete="username"
              className="w-full rounded border border-ink-line bg-ink/40 px-3 py-2 text-sm font-mono text-fg outline-none transition-colors focus:border-accent focus:shadow-ring"
            />
          </label>

          <label className="block">
            <span className="mb-1 block text-2xs font-medium uppercase tracking-[0.18em] text-fg-dim">
              Passphrase
            </span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
              className="w-full rounded border border-ink-line bg-ink/40 px-3 py-2 text-sm font-mono text-fg outline-none transition-colors focus:border-accent focus:shadow-ring"
            />
          </label>

          {error !== null ? <ErrorMessage error={error} /> : null}

          <button
            type="submit"
            disabled={submitting}
            className="group relative w-full overflow-hidden rounded border border-accent/40 bg-accent-muted px-3 py-2.5 text-sm font-medium text-fg transition-colors hover:border-accent hover:bg-accent/30 disabled:opacity-50"
          >
            <span className="relative z-10 flex items-center justify-center gap-2">
              {submitting ? (
                <Spinner label="Authenticating…" />
              ) : (
                <>
                  <span>Sign in</span>
                  <svg
                    viewBox="0 0 16 16"
                    fill="none"
                    className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5"
                    aria-hidden
                  >
                    <path
                      d="M3 8h10M9 4l4 4-4 4"
                      stroke="currentColor"
                      strokeWidth="1.6"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </>
              )}
            </span>
            {/* scan line — only while submitting */}
            {submitting ? (
              <span
                aria-hidden
                className="pointer-events-none absolute inset-y-0 left-0 w-1/3 animate-scan bg-gradient-to-r from-transparent via-accent/30 to-transparent"
              />
            ) : null}
          </button>

          {githubEnabled ? (
            <>
              <div className="relative flex items-center gap-3 text-2xs uppercase tracking-[0.3em] text-fg-dim">
                <span className="h-px flex-1 bg-ink-line" />
                <span>or</span>
                <span className="h-px flex-1 bg-ink-line" />
              </div>
              <a
                href={buildOAuthUrl("github", nextPath)}
                className="group flex w-full items-center justify-center gap-2 rounded border border-ink-line bg-ink/40 px-3 py-2.5 text-sm font-medium text-fg transition-colors hover:border-accent/60 hover:bg-ink-subtle"
              >
                <svg
                  viewBox="0 0 16 16"
                  fill="currentColor"
                  aria-hidden
                  className="h-4 w-4"
                >
                  <path
                    fillRule="evenodd"
                    d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"
                  />
                </svg>
                <span>Sign in with GitHub</span>
              </a>
            </>
          ) : null}

          <p className="text-2xs leading-relaxed text-fg-dim">
            Accounts are provisioned by an admin via the{" "}
            <code className="font-mono text-fg-muted">Members</code> panel or
            the{" "}
            <code className="font-mono text-fg-muted">
              reckora-api create-user
            </code>{" "}
            CLI.
          </p>
        </form>

        <div className="mt-4 text-center text-2xs uppercase tracking-[0.22em] text-fg-dim">
          Reckora · classified workspace
        </div>
      </div>
    </div>
  );
}
