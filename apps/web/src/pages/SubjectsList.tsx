import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api, unwrap } from "@/api/client";
import type { SubjectSummary } from "@/api/types";
import { EmptyState } from "@/components/EmptyState";
import { ErrorMessage } from "@/components/ErrorMessage";
import { SkeletonList } from "@/components/Skeleton";
import { formatRelativeTime, shortId } from "@/lib/format";

async function fetchSubjects(): Promise<SubjectSummary[]> {
  return unwrap(await api.GET("/api/v1/subjects"));
}

const KIND_TONE: Record<string, string> = {
  username: "bg-accent-soft text-accent border-accent/30",
  email: "bg-accent-soft text-accent border-accent/30",
  domain: "bg-ok-soft text-ok border-ok/30",
  wallet: "bg-alert-soft text-alert border-alert/30",
};

function kindToneClass(kind: string): string {
  return (
    KIND_TONE[kind.toLowerCase()] ??
    "bg-ink-subtle text-fg-muted border-ink-line"
  );
}

export function SubjectsListPage() {
  const query = useQuery({
    queryKey: ["subjects"],
    queryFn: fetchSubjects,
  });
  const [filter, setFilter] = useState("");

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q || !query.data) return query.data ?? [];
    return query.data.filter(
      (s) =>
        s.seed.value.toLowerCase().includes(q) ||
        s.seed.kind.toLowerCase().includes(q) ||
        (s.owner_username?.toLowerCase().includes(q) ?? false) ||
        s.id.toLowerCase().includes(q),
    );
  }, [query.data, filter]);

  return (
    <section className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-2xs font-medium uppercase tracking-[0.22em] text-fg-dim">
            Investigation
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-snug text-fg">
            Subjects
          </h1>
          <p className="mt-1 text-sm text-fg-muted">
            Dossiers visible to your account.
          </p>
        </div>
        <Link
          to="/investigations/new"
          className="inline-flex items-center gap-2 rounded border border-accent/40 bg-accent-muted px-3 py-1.5 text-sm font-medium text-fg transition-colors hover:border-accent hover:bg-accent/30"
        >
          <svg viewBox="0 0 16 16" fill="none" className="h-3.5 w-3.5" aria-hidden>
            <path
              d="M8 3v10M3 8h10"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
          </svg>
          New investigation
        </Link>
      </header>

      <div className="flex items-center justify-between gap-3">
        <div className="relative flex-1 max-w-md">
          <svg
            viewBox="0 0 16 16"
            fill="none"
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-dim"
            aria-hidden
          >
            <circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.4" />
            <path
              d="m10.5 10.5 3 3"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
            />
          </svg>
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter by identifier, owner, or fingerprint…"
            className="w-full rounded border border-ink-line bg-ink-panel pl-8 pr-3 py-1.5 text-sm text-fg placeholder:text-fg-dim outline-none transition-colors focus:border-accent focus:shadow-ring"
          />
        </div>
        {query.data ? (
          <div className="text-2xs uppercase tracking-[0.18em] text-fg-dim">
            {filtered.length}
            <span className="mx-1 text-fg-dim">/</span>
            {query.data.length} subjects
          </div>
        ) : null}
      </div>

      {query.isPending ? <SkeletonList count={5} /> : null}
      {query.error ? <ErrorMessage error={query.error} /> : null}

      {query.data && query.data.length === 0 ? (
        <EmptyState
          icon="◎"
          title="No subjects yet"
          description="Start by running a new investigation on a username, email, domain, or wallet."
          action={
            <Link
              to="/investigations/new"
              className="rounded border border-accent/40 bg-accent-muted px-3 py-1 text-xs hover:border-accent hover:bg-accent/30"
            >
              New investigation
            </Link>
          }
        />
      ) : null}

      {query.data && query.data.length > 0 && filtered.length === 0 ? (
        <EmptyState
          icon="∅"
          title="No matches"
          description={`Nothing matches "${filter}".`}
        />
      ) : null}

      {filtered.length > 0 ? (
        <ul className="divide-y divide-ink-line overflow-hidden rounded-lg border border-ink-line bg-ink-panel">
          {filtered.map((subject) => (
            <SubjectRow key={subject.id} subject={subject} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function SubjectRow({ subject }: { subject: SubjectSummary }) {
  return (
    <li className="group transition-colors hover:bg-ink-subtle/50">
      <Link
        to={`/subjects/${subject.id}`}
        className="flex items-center gap-4 px-4 py-3"
      >
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span
              className={`rounded border px-1.5 py-0.5 font-mono text-2xs uppercase tracking-[0.12em] ${kindToneClass(
                subject.seed.kind,
              )}`}
            >
              {subject.seed.kind}
            </span>
            <span className="truncate font-medium text-fg">
              {subject.seed.value}
            </span>
            {subject.owner_username ? (
              <span className="rounded border border-ink-line bg-ink-subtle px-1.5 py-0.5 text-2xs text-fg-muted">
                @{subject.owner_username}
              </span>
            ) : null}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-fg-dim">
            <span className="font-mono uppercase tracking-[0.08em]">
              {shortId(subject.id)}
            </span>
            <span>·</span>
            <span>created {formatRelativeTime(subject.created_at)}</span>
            <span>·</span>
            <span>
              <span className="text-fg-muted">{subject.identifier_count}</span>{" "}
              ids
            </span>
            <span>
              <span className="text-fg-muted">{subject.trace_count}</span>{" "}
              traces
            </span>
          </div>
        </div>
        <svg
          viewBox="0 0 16 16"
          fill="none"
          className="h-4 w-4 shrink-0 text-fg-dim transition-transform group-hover:translate-x-0.5 group-hover:text-accent"
          aria-hidden
        >
          <path
            d="M5 3l5 5-5 5"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </Link>
    </li>
  );
}
