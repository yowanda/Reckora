import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { writeToken } from "@/api/client";
import { LogoMark } from "@/components/LogoMark";
import { Spinner } from "@/components/Spinner";
import { useAuth } from "@/lib/auth";

/** Pull the URL fragment params (``window.location.hash``) once.
 *
 * The backend OAuth callback redirects to ``/auth/callback#token=...&next=/...``.
 * Fragments are never sent to the API server or recorded in the
 * ``Referer`` header on the next navigation, so they're a safer
 * hand-off channel for the JWT than query parameters.
 */
function readFragment(): URLSearchParams {
  const raw = window.location.hash.replace(/^#/, "");
  return new URLSearchParams(raw);
}

function isSafeNext(value: string | null): value is string {
  if (value === null || value === "") {
    return false;
  }
  // Only allow same-origin relative paths to avoid open-redirect bugs.
  return value.startsWith("/") && !value.startsWith("//");
}

export function OAuthCallbackPage() {
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = readFragment();
    const token = params.get("token");
    const next = params.get("next");
    const target = isSafeNext(next) ? next : "/subjects";

    if (!token) {
      setError(
        params.get("error") ??
          "missing token in oauth callback; please try signing in again",
      );
      return;
    }

    // Persist the JWT, hydrate the auth context, then clear the
    // fragment so the token never lingers in the URL bar after the
    // navigation completes.
    writeToken(token);
    window.history.replaceState(null, "", window.location.pathname);
    void refresh().then(() => {
      navigate(target, { replace: true });
    });
  }, [navigate, refresh]);

  if (error !== null) {
    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <div className="w-full max-w-md space-y-4 rounded-lg border border-ink-line bg-ink-panel/90 p-6 text-center shadow-panel">
          <div className="flex items-center justify-center gap-3">
            <LogoMark className="h-8 w-8" />
            <span className="text-base font-semibold text-fg">Reckora</span>
          </div>
          <h1 className="text-sm font-medium text-danger">
            Sign-in could not complete
          </h1>
          <p className="font-mono text-xs text-fg-muted">{error}</p>
          <button
            type="button"
            onClick={() => navigate("/login", { replace: true })}
            className="rounded border border-ink-line bg-ink/40 px-3 py-1.5 text-xs text-fg hover:border-accent/60"
          >
            Back to sign-in
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="flex flex-col items-center gap-3 text-fg-muted">
        <Spinner />
        <span className="text-xs uppercase tracking-[0.3em]">
          Completing sign-in…
        </span>
      </div>
    </div>
  );
}
