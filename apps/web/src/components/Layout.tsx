import { Link, NavLink, Outlet } from "react-router-dom";

import { useAuth } from "@/lib/auth";

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  `px-3 py-1.5 rounded text-sm transition-colors ${
    isActive
      ? "bg-bg-subtle text-zinc-100"
      : "text-zinc-400 hover:text-zinc-100 hover:bg-bg-subtle"
  }`;

export function Layout() {
  const { state, logout } = useAuth();
  const username =
    state.status === "authenticated" ? state.user.username : "anonymous";
  const role = state.status === "authenticated" ? state.user.role : "";
  const isAdmin = role === "admin";

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-border bg-bg-panel">
        <div className="mx-auto max-w-6xl flex items-center gap-4 px-4 py-3">
          <Link to="/" className="font-semibold tracking-tight">
            Reckora
          </Link>
          <nav className="flex items-center gap-1">
            <NavLink to="/subjects" className={navLinkClass}>
              Subjects
            </NavLink>
            <NavLink to="/investigations/new" className={navLinkClass}>
              New investigation
            </NavLink>
            <NavLink to="/me/mentions" className={navLinkClass}>
              Mentions
            </NavLink>
            <NavLink to="/me/pins" className={navLinkClass}>
              Pinned
            </NavLink>
            <NavLink to="/me/watching" className={navLinkClass}>
              Watching
            </NavLink>
            {isAdmin ? (
              <NavLink to="/admin/members" className={navLinkClass}>
                Members
              </NavLink>
            ) : null}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm text-zinc-400">
            <span>
              {username}
              {role ? ` · ${role}` : ""}
            </span>
            <button
              type="button"
              onClick={logout}
              className="rounded border border-border bg-bg-subtle px-2 py-1 text-zinc-300 hover:text-zinc-100"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
