import { Link, useLocation } from "react-router-dom";

interface Crumb {
  label: string;
  to?: string;
}

const STATIC_LABELS: Record<string, string> = {
  subjects: "Subjects",
  investigations: "Investigations",
  new: "New investigation",
  me: "My queue",
  mentions: "Mentions",
  pins: "Pinned",
  watching: "Watching",
  admin: "Admin",
  members: "Members",
  login: "Sign in",
};

function humanise(slug: string): string {
  return STATIC_LABELS[slug] ?? slug;
}

/**
 * Lightweight breadcrumbs derived from the URL. We intentionally do
 * NOT name dossiers here (dossier-aware crumbs would have to refetch
 * data we already render on the page, and the page header is the
 * place that already prints the subject identifier prominently).
 * Instead a dossier ID is shown as a short fingerprint so the trail
 * still reads correctly.
 */
export function Breadcrumbs() {
  const location = useLocation();
  const segments = location.pathname.split("/").filter(Boolean);

  if (segments.length === 0) return null;

  const crumbs: Crumb[] = [];
  let path = "";
  for (let i = 0; i < segments.length; i += 1) {
    const seg = segments[i];
    path += `/${seg}`;
    const prev = segments[i - 1];
    let label: string;
    if (prev === "subjects" && seg.length > 6) {
      label = `subject ${seg.slice(0, 6)}`;
    } else {
      label = humanise(seg);
    }
    crumbs.push({ label, to: i < segments.length - 1 ? path : undefined });
  }

  return (
    <nav
      aria-label="Breadcrumb"
      className="flex min-w-0 items-center gap-1.5 text-xs text-fg-muted"
    >
      {crumbs.map((c, idx) => (
        <span key={idx} className="flex min-w-0 items-center gap-1.5">
          {idx > 0 ? <span className="text-fg-dim">/</span> : null}
          {c.to ? (
            <Link to={c.to} className="truncate hover:text-fg">
              {c.label}
            </Link>
          ) : (
            <span className="truncate text-fg">{c.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}
