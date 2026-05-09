import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { api, unwrap } from "@/api/client";
import type { InvestigationRequest, SavedDossierPayload } from "@/api/types";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Spinner } from "@/components/Spinner";

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

async function postInvestigation(
  body: InvestigationRequest,
): Promise<SavedDossierPayload> {
  return unwrap(await api.POST("/api/v1/investigations", { body }));
}

export function NewInvestigationPage() {
  const navigate = useNavigate();
  const [kind, setKind] = useState<Kind>("username");
  const [value, setValue] = useState("");
  const [archive, setArchive] = useState(false);
  const [screenshot, setScreenshot] = useState(false);
  const [ai, setAi] = useState(false);
  const [breach, setBreach] = useState(false);
  const [anchor, setAnchor] = useState(false);

  const mutation = useMutation({
    mutationFn: postInvestigation,
    onSuccess: (data) => {
      navigate(`/subjects/${data.id}`);
    },
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    mutation.mutate({
      seed: { kind, value },
      archive,
      screenshot,
      ai,
      breach,
      anchor,
    });
  }

  return (
    <section className="mx-auto max-w-xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold">New investigation</h1>
        <p className="text-sm text-zinc-500">
          Runs the orchestrator against a seed identifier and stores the
          dossier.
        </p>
      </div>
      <form
        onSubmit={onSubmit}
        className="space-y-4 rounded border border-border bg-bg-panel p-5"
      >
        <div className="grid grid-cols-3 gap-3">
          <label className="col-span-1 text-sm">
            <span className="mb-1 block text-zinc-400">Kind</span>
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as Kind)}
              className="w-full rounded border border-border bg-bg-subtle px-2 py-1.5"
            >
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </label>
          <label className="col-span-2 text-sm">
            <span className="mb-1 block text-zinc-400">Value</span>
            <input
              required
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="octocat / example.com / +14155552671 …"
              className="w-full rounded border border-border bg-bg-subtle px-2 py-1.5 outline-none focus:border-accent"
            />
          </label>
        </div>
        <fieldset className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-3">
          <Toggle label="archive" checked={archive} onChange={setArchive} />
          <Toggle
            label="screenshot"
            checked={screenshot}
            onChange={setScreenshot}
          />
          <Toggle label="ai" checked={ai} onChange={setAi} />
          <Toggle label="breach" checked={breach} onChange={setBreach} />
          <Toggle label="anchor" checked={anchor} onChange={setAnchor} />
        </fieldset>
        {mutation.error ? <ErrorMessage error={mutation.error} /> : null}
        <button
          type="submit"
          disabled={mutation.isPending}
          className="w-full rounded bg-accent-muted px-3 py-2 text-sm font-medium hover:bg-accent disabled:opacity-50"
        >
          {mutation.isPending ? <Spinner label="Investigating…" /> : "Run"}
        </button>
      </form>
    </section>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 rounded border border-border bg-bg-subtle px-2 py-1.5">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="font-mono text-xs text-zinc-300">{label}</span>
    </label>
  );
}
