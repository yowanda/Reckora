import { NavLink } from "react-router-dom";

import { useAuth } from "@/lib/auth";

import { LogoMark } from "./LogoMark";

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  [
    "group flex items-center gap-3 rounded px-2.5 py-2 text-sm transition-colors",
    isActive
      ? "bg-ink-subtle text-fg shadow-[inset_2px_0_0_0_theme(colors.accent.DEFAULT)]"
      : "text-fg-muted hover:bg-ink-subtle/60 hover:text-fg",
  ].join(" ");

const sectionTitleClass =
  "px-2 pb-1.5 pt-3 text-2xs font-medium uppercase tracking-[0.18em] text-fg-dim";

interface IconProps {
  className?: string;
}

const SubjectsIcon = ({ className = "h-4 w-4" }: IconProps) => (
  <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
    <path
      d="M2.5 4h11M2.5 8h11M2.5 12h7"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
    />
  </svg>
);

const NewInvestigationIcon = ({ className = "h-4 w-4" }: IconProps) => (
  <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
    <circle cx="7" cy="7" r="4.25" stroke="currentColor" strokeWidth="1.4" />
    <path
      d="m10.2 10.2 3 3"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
    />
    <path
      d="M7 5v4M5 7h4"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
    />
  </svg>
);

const MentionsIcon = ({ className = "h-4 w-4" }: IconProps) => (
  <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
    <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.4" />
    <circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.4" />
    <path
      d="M14 8v1a2 2 0 0 1-4 0V6"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
    />
  </svg>
);

const PinnedIcon = ({ className = "h-4 w-4" }: IconProps) => (
  <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
    <path
      d="M9.5 2.5 13.5 6.5 11.5 7l-1 4-3-3-3 3 1-4-1-1L9.5 2.5z"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinejoin="round"
    />
    <path
      d="m6 10-3 3"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
    />
  </svg>
);

const WatchingIcon = ({ className = "h-4 w-4" }: IconProps) => (
  <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
    <path
      d="M1.5 8s2.5-4.5 6.5-4.5S14.5 8 14.5 8s-2.5 4.5-6.5 4.5S1.5 8 1.5 8z"
      stroke="currentColor"
      strokeWidth="1.4"
    />
    <circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.4" />
  </svg>
);

const MembersIcon = ({ className = "h-4 w-4" }: IconProps) => (
  <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
    <circle cx="6" cy="6" r="2.25" stroke="currentColor" strokeWidth="1.4" />
    <path
      d="M2 13c.5-2.2 2.2-3.5 4-3.5s3.5 1.3 4 3.5"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
    />
    <circle cx="11.5" cy="5.5" r="1.75" stroke="currentColor" strokeWidth="1.4" />
    <path
      d="M10.5 9.5c1.5 0 3 1 3.5 3"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
    />
  </svg>
);

interface SidebarProps {
  onClose?: () => void;
  onOpenPalette: () => void;
}

export function Sidebar({ onClose, onOpenPalette }: SidebarProps) {
  const { state, logout } = useAuth();
  const username =
    state.status === "authenticated" ? state.user.username : "anonymous";
  const role = state.status === "authenticated" ? state.user.role : "";
  const isAdmin = role === "admin";

  return (
    <aside
      className="flex h-full w-60 shrink-0 flex-col border-r border-ink-line bg-ink-panel/90 backdrop-blur"
      aria-label="Primary navigation"
    >
      <div className="flex items-center gap-2 px-4 pb-3 pt-4">
        <LogoMark className="h-7 w-7" />
        <div className="leading-tight">
          <div className="text-sm font-semibold tracking-snug text-fg">
            Reckora
          </div>
          <div className="text-2xs uppercase tracking-[0.22em] text-fg-dim">
            forensic OSINT
          </div>
        </div>
      </div>

      <button
        type="button"
        onClick={onOpenPalette}
        className="mx-3 mt-1 flex items-center gap-2 rounded border border-ink-line bg-ink/40 px-2.5 py-1.5 text-left text-xs text-fg-muted transition-colors hover:border-accent/40 hover:text-fg"
      >
        <svg viewBox="0 0 16 16" fill="none" className="h-3.5 w-3.5" aria-hidden>
          <circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.4" />
          <path
            d="m10.5 10.5 3 3"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
          />
        </svg>
        <span className="flex-1">Quick action…</span>
        <span className="rk-kbd">⌘K</span>
      </button>

      <nav className="mt-3 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
        <div className={sectionTitleClass}>Investigation</div>
        <NavLink to="/subjects" className={navLinkClass} onClick={onClose}>
          <SubjectsIcon /> Subjects
        </NavLink>
        <NavLink
          to="/investigations/new"
          className={navLinkClass}
          onClick={onClose}
        >
          <NewInvestigationIcon /> New investigation
        </NavLink>

        <div className={sectionTitleClass}>My queue</div>
        <NavLink to="/me/mentions" className={navLinkClass} onClick={onClose}>
          <MentionsIcon /> Mentions
        </NavLink>
        <NavLink to="/me/pins" className={navLinkClass} onClick={onClose}>
          <PinnedIcon /> Pinned
        </NavLink>
        <NavLink to="/me/watching" className={navLinkClass} onClick={onClose}>
          <WatchingIcon /> Watching
        </NavLink>

        {isAdmin ? (
          <>
            <div className={sectionTitleClass}>Admin</div>
            <NavLink
              to="/admin/members"
              className={navLinkClass}
              onClick={onClose}
            >
              <MembersIcon /> Members
            </NavLink>
          </>
        ) : null}
      </nav>

      <div className="border-t border-ink-line px-3 py-3 text-xs">
        <div className="flex items-center gap-2">
          <div
            className={`rk-live-dot h-2 w-2 rounded-full ${
              isAdmin ? "bg-alert" : "bg-ok"
            }`}
            aria-hidden
          />
          <div className="min-w-0 flex-1 leading-tight">
            <div className="truncate font-medium text-fg">{username}</div>
            <div className="text-2xs uppercase tracking-[0.22em] text-fg-dim">
              {role}
            </div>
          </div>
          <button
            type="button"
            onClick={logout}
            className="rounded border border-ink-line bg-ink-subtle px-2 py-1 text-2xs uppercase tracking-[0.18em] text-fg-muted transition-colors hover:border-danger/50 hover:text-danger"
          >
            Sign out
          </button>
        </div>
      </div>
    </aside>
  );
}
