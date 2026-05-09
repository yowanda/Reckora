import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { useNavigate, useParams } from "react-router-dom";

import { api, unwrap } from "@/api/client";
import type { SavedDossierPayload } from "@/api/types";
import { DossierCards, viewDossier } from "@/components/Dossier";
import { ErrorMessage } from "@/components/ErrorMessage";
import { ActivityFeed } from "@/components/Phase5/Activity";
import { Assignees } from "@/components/Phase5/Assignees";
import { Comments } from "@/components/Phase5/Comments";
import { LabelChips } from "@/components/Phase5/Labels";
import { PrivateNote } from "@/components/Phase5/Notes";
import { PinToggle } from "@/components/Phase5/PinToggle";
import { StatusPicker } from "@/components/Phase5/Status";
import { Todos } from "@/components/Phase5/Todos";
import { WatchToggle } from "@/components/Phase5/Watchers";
import { CrossReferences } from "@/components/Phase5/Xref";
import { Spinner } from "@/components/Spinner";
import { formatRelativeTime, shortId } from "@/lib/format";
import { describeError, useToast } from "@/lib/toast";

async function fetchSubject(id: string): Promise<SavedDossierPayload> {
  return unwrap(
    await api.GET("/api/v1/subjects/{subject_id}", {
      params: { path: { subject_id: id } },
    }),
  );
}

async function fetchDossier(id: string): Promise<string> {
  const result = await api.GET("/api/v1/subjects/{subject_id}/dossier", {
    params: {
      path: { subject_id: id },
      query: { format: "md" },
    },
    parseAs: "text",
  });
  if (result.error !== undefined || result.data === undefined) {
    throw new Error(`Failed to fetch dossier (${result.response.status})`);
  }
  return result.data as unknown as string;
}

async function recordVisit(subjectId: string): Promise<void> {
  unwrap(
    await api.POST("/api/v1/subjects/{subject_id}/visits/me", {
      params: { path: { subject_id: subjectId } },
    }),
  );
}

async function deleteSubject(id: string): Promise<void> {
  unwrap(
    await api.DELETE("/api/v1/subjects/{subject_id}", {
      params: { path: { subject_id: id } },
    }),
  );
}

interface SeedSnippet {
  kind: string;
  value: string;
}

function extractSeed(payload: SavedDossierPayload): SeedSnippet | null {
  const subject = payload.subject;
  if (!subject || typeof subject !== "object") {
    return null;
  }
  const kind =
    (subject as Record<string, unknown>).kind ??
    (subject as Record<string, unknown>).type;
  const value = (subject as Record<string, unknown>).value;
  if (typeof kind !== "string" || typeof value !== "string") {
    // Fall back to the structured `seed_identifier` shape used by the
    // canonical `to_dossier_dict()` payload.
    const seed = (subject as Record<string, unknown>).seed_identifier;
    if (seed !== null && typeof seed === "object") {
      const seedRecord = seed as Record<string, unknown>;
      const seedKind = seedRecord.type;
      const seedValue = seedRecord.value;
      if (typeof seedKind === "string" && typeof seedValue === "string") {
        return { kind: seedKind, value: seedValue };
      }
    }
    return null;
  }
  return { kind, value };
}

type ViewMode = "cards" | "markdown";

const VIEW_MODE_STORAGE_KEY = "reckora.dossier.viewMode";

function readStoredViewMode(): ViewMode {
  if (typeof window === "undefined") {
    return "cards";
  }
  const raw = window.localStorage.getItem(VIEW_MODE_STORAGE_KEY);
  return raw === "markdown" ? "markdown" : "cards";
}

export function SubjectDetailPage() {
  const params = useParams<{ subjectId: string }>();
  const subjectId = params.subjectId ?? "";
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();

  const [viewMode, setViewMode] = useState<ViewMode>(readStoredViewMode);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    window.localStorage.setItem(VIEW_MODE_STORAGE_KEY, viewMode);
  }, [viewMode]);

  const subject = useQuery({
    queryKey: ["subjects", subjectId, "summary"],
    queryFn: () => fetchSubject(subjectId),
    enabled: subjectId !== "",
  });
  const dossier = useQuery({
    queryKey: ["subjects", subjectId, "dossier", "md"],
    queryFn: () => fetchDossier(subjectId),
    // Only spend the round-trip when the analyst actually wants the
    // markdown view; the cards view derives everything from `subject`.
    enabled: subjectId !== "" && viewMode === "markdown",
  });

  useEffect(() => {
    if (subjectId === "") {
      return;
    }
    void recordVisit(subjectId).catch(() => {
      // best-effort; ignore
    });
  }, [subjectId]);

  const remove = useMutation({
    mutationFn: deleteSubject,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["subjects"] });
      toast.push("success", "Subject deleted");
      navigate("/subjects");
    },
    onError: (error) => toast.push("error", describeError(error)),
  });

  const dossierView = useMemo(
    () => (subject.data ? viewDossier(subject.data) : null),
    [subject.data],
  );

  if (subjectId === "") {
    return null;
  }

  const seed = subject.data ? extractSeed(subject.data) : null;

  return (
    <section className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="space-y-5">
        <header className="overflow-hidden rounded-lg border border-ink-line bg-ink-panel">
          <div className="border-b border-ink-line bg-ink-subtle/40 px-4 py-2 text-2xs font-medium uppercase tracking-[0.2em] text-fg-dim">
            Dossier
          </div>
          <div className="space-y-3 p-4">
            {subject.isPending ? <Spinner label="Loading dossier…" /> : null}
            {subject.error ? <ErrorMessage error={subject.error} /> : null}
            {subject.data ? (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded border border-accent/30 bg-accent-soft px-1.5 py-0.5 font-mono text-2xs uppercase tracking-[0.12em] text-accent">
                    {seed?.kind ?? "subject"}
                  </span>
                  <h1 className="text-xl font-semibold tracking-snug text-fg">
                    {seed?.value ?? "(unknown seed)"}
                  </h1>
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-fg-dim">
                  <span className="font-mono uppercase tracking-[0.08em]">
                    {shortId(subject.data.id, 12)}
                  </span>
                  <span>·</span>
                  <span>created {formatRelativeTime(subject.data.created_at)}</span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPicker subjectId={subjectId} />
                  <WatchToggle subjectId={subjectId} />
                  <PinToggle subjectId={subjectId} />
                  <button
                    type="button"
                    onClick={() => {
                      if (
                        confirm("Delete this subject? This cannot be undone.")
                      ) {
                        remove.mutate(subjectId);
                      }
                    }}
                    className="ml-auto rounded border border-danger/40 bg-danger-soft px-2 py-1 text-xs text-danger transition-colors hover:border-danger hover:bg-danger/20"
                  >
                    Delete
                  </button>
                </div>
                <LabelChips subjectId={subjectId} />
              </>
            ) : null}
          </div>
        </header>

        <article className="overflow-hidden rounded-lg border border-ink-line bg-ink-panel">
          <div className="flex items-center justify-between gap-3 border-b border-ink-line bg-ink-subtle/40 px-4 py-2 text-2xs font-medium uppercase tracking-[0.2em] text-fg-dim">
            <span>Findings</span>
            <ViewModeToggle value={viewMode} onChange={setViewMode} />
          </div>
          <div className="p-4">
            {viewMode === "cards" ? (
              <>
                {subject.isPending ? (
                  <Spinner label="Loading dossier…" />
                ) : null}
                {subject.error ? (
                  <ErrorMessage error={subject.error} />
                ) : null}
                {dossierView ? <DossierCards view={dossierView} /> : null}
              </>
            ) : (
              <>
                {dossier.isPending ? (
                  <Spinner label="Rendering dossier…" />
                ) : null}
                {dossier.error ? <ErrorMessage error={dossier.error} /> : null}
                {dossier.data ? (
                  <div className="prose prose-invert prose-sm max-w-none">
                    <ReactMarkdown>{dossier.data}</ReactMarkdown>
                  </div>
                ) : null}
              </>
            )}
          </div>
        </article>

        <Comments subjectId={subjectId} />
      </div>
      <aside className="space-y-4">
        <ActivityFeed subjectId={subjectId} />
        <Assignees subjectId={subjectId} />
        <PrivateNote subjectId={subjectId} />
        <Todos subjectId={subjectId} />
        <CrossReferences subjectId={subjectId} />
      </aside>
    </section>
  );
}

function ViewModeToggle({
  value,
  onChange,
}: {
  value: ViewMode;
  onChange: (next: ViewMode) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="Dossier view"
      className="flex shrink-0 items-center gap-0.5 rounded border border-ink-line bg-ink-panel/80 p-0.5 normal-case tracking-normal"
    >
      <ViewModeButton
        active={value === "cards"}
        onClick={() => onChange("cards")}
      >
        Cards
      </ViewModeButton>
      <ViewModeButton
        active={value === "markdown"}
        onClick={() => onChange("markdown")}
      >
        Markdown
      </ViewModeButton>
    </div>
  );
}

function ViewModeButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: string;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`rounded px-2 py-0.5 text-2xs font-medium uppercase tracking-[0.12em] transition-colors ${
        active
          ? "bg-accent-soft text-accent"
          : "text-fg-muted hover:text-fg"
      }`}
    >
      {children}
    </button>
  );
}
