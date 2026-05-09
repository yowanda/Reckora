import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { api, unwrap } from "@/api/client";
import type { InvestigationRequest, SavedDossierPayload } from "@/api/types";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Spinner } from "@/components/Spinner";
import { describeError, useToast } from "@/lib/toast";

const KINDS = [
  "username",
  "email",
  "domain",
  "url",
  "phone",
  "wallet",
  "avatar",
] as const;

type Kind = (typeof KINDS)[number];

const TOGGLES: Array<{
  label: string;
  key: "archive" | "screenshot" | "ai" | "breach" | "anchor";
  description: string;
}> = [
  {
    label: "archive",
    key: "archive",
    description: "Save WebArchive snapshots of every URL hit.",
  },
  {
    label: "screenshot",
    key: "screenshot",
    description: "Capture full-page PNG of every URL surface.",
  },
  {
    label: "ai",
    key: "ai",
    description: "Summarise + hypothesise via the configured LLM.",
  },
  {
    label: "breach",
    key: "breach",
    description: "Check the email/username against breach corpora.",
  },
  {
    label: "anchor",
    key: "anchor",
    description: "Pin the dossier to its evidence chain.",
  },
];

const PLACEHOLDER: Record<Kind, string> = {
  username: "octocat",
  email: "person@example.com",
  domain: "example.com",
  url: "https://example.com/profile",
  phone: "+14155552671",
  wallet: "0xabc…",
  avatar: "https://example.com/avatar.png",
};

async function postInvestigation(
  body: InvestigationRequest,
): Promise<SavedDossierPayload> {
  return unwrap(await api.POST("/api/v1/investigations", { body }));
}

export function NewInvestigationPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [kind, setKind] = useState<Kind>("username");
  const [value, setValue] = useState("");
  const [flags, setFlags] = useState<Record<(typeof TOGGLES)[number]["key"], boolean>>({
    archive: false,
    screenshot: false,
    ai: false,
    breach: false,
    anchor: false,
  });

  const mutation = useMutation({
    mutationFn: postInvestigation,
    onSuccess: (data) => {
      toast.push("success", "Investigation saved");
      navigate(`/subjects/${data.id}`);
    },
    onError: (error) => toast.push("error", describeError(error)),
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    mutation.mutate({
      seed: { kind, value },
      ...flags,
    });
  }

  return (
    <section className="mx-auto max-w-2xl space-y-5">
      <header>
        <div className="text-2xs font-medium uppercase tracking-[0.22em] text-fg-dim">
          Investigation
        </div>
        <h1 className="mt-1 text-2xl font-semibold tracking-snug text-fg">
          New investigation
        </h1>
        <p className="mt-1 text-sm text-fg-muted">
          Seed a dossier from any single identifier — Reckora's orchestrator
          fans out across the configured collectors and writes the result back
          to a stable subject record.
        </p>
      </header>

      <form
        onSubmit={onSubmit}
        className="overflow-hidden rounded-lg border border-ink-line bg-ink-panel"
      >
        <div className="border-b border-ink-line bg-ink-subtle/40 px-4 py-2 text-2xs font-medium uppercase tracking-[0.2em] text-fg-dim">
          Seed identifier
        </div>
        <div className="space-y-4 p-5">
          <div className="grid gap-3 sm:grid-cols-[140px_1fr]">
            <label className="block">
              <span className="mb-1 block text-2xs font-medium uppercase tracking-[0.18em] text-fg-dim">
                Kind
              </span>
              <select
                value={kind}
                onChange={(e) => setKind(e.target.value as Kind)}
                className="w-full rounded border border-ink-line bg-ink/40 px-2 py-2 text-sm font-mono text-fg outline-none transition-colors focus:border-accent focus:shadow-ring"
              >
                {KINDS.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block text-2xs font-medium uppercase tracking-[0.18em] text-fg-dim">
                Value
              </span>
              <input
                required
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder={PLACEHOLDER[kind]}
                className="w-full rounded border border-ink-line bg-ink/40 px-3 py-2 text-sm font-mono text-fg outline-none transition-colors placeholder:text-fg-dim focus:border-accent focus:shadow-ring"
              />
            </label>
          </div>

          <div>
            <div className="mb-2 text-2xs font-medium uppercase tracking-[0.18em] text-fg-dim">
              Collector flags
            </div>
            <fieldset className="grid gap-2 sm:grid-cols-2">
              {TOGGLES.map((t) => (
                <Toggle
                  key={t.key}
                  label={t.label}
                  description={t.description}
                  checked={flags[t.key]}
                  onChange={(next) =>
                    setFlags((prev) => ({ ...prev, [t.key]: next }))
                  }
                />
              ))}
            </fieldset>
          </div>

          {mutation.error ? <ErrorMessage error={mutation.error} /> : null}

          <div className="flex items-center justify-between border-t border-ink-line pt-4">
            <p className="text-2xs text-fg-dim">
              Each run is logged + visible in <span className="font-mono">Activity</span>.
            </p>
            <button
              type="submit"
              disabled={mutation.isPending}
              className="inline-flex items-center gap-2 rounded border border-accent/40 bg-accent-muted px-4 py-2 text-sm font-medium text-fg transition-colors hover:border-accent hover:bg-accent/30 disabled:opacity-50"
            >
              {mutation.isPending ? (
                <Spinner label="Investigating…" />
              ) : (
                <>
                  <span>Run investigation</span>
                  <svg
                    viewBox="0 0 16 16"
                    fill="none"
                    className="h-3.5 w-3.5"
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
            </button>
          </div>
        </div>
      </form>
    </section>
  );
}

function Toggle({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <label
      className={[
        "flex cursor-pointer items-start gap-3 rounded border px-3 py-2 transition-colors",
        checked
          ? "border-accent/40 bg-accent-soft"
          : "border-ink-line bg-ink-subtle/40 hover:border-ink-line2",
      ].join(" ")}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-3.5 w-3.5 cursor-pointer accent-accent"
      />
      <div className="min-w-0">
        <div className="font-mono text-xs uppercase tracking-[0.12em] text-fg">
          {label}
        </div>
        <div className="text-2xs text-fg-dim">{description}</div>
      </div>
    </label>
  );
}
