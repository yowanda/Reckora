import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "@/lib/auth";

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

interface Command {
  id: string;
  label: string;
  hint?: string;
  group: "Investigation" | "My queue" | "Admin" | "Account";
  action: () => void;
  adminOnly?: boolean;
}

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const navigate = useNavigate();
  const { state, logout } = useAuth();
  const isAdmin = state.status === "authenticated" && state.user.role === "admin";
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const commands = useMemo<Command[]>(() => {
    const all: Command[] = [
      {
        id: "go-subjects",
        label: "Open Subjects",
        hint: "Browse all dossiers",
        group: "Investigation",
        action: () => navigate("/subjects"),
      },
      {
        id: "new-investigation",
        label: "Start a new investigation",
        hint: "From an identifier",
        group: "Investigation",
        action: () => navigate("/investigations/new"),
      },
      {
        id: "go-mentions",
        label: "Open Mentions",
        group: "My queue",
        action: () => navigate("/me/mentions"),
      },
      {
        id: "go-pins",
        label: "Open Pinned",
        group: "My queue",
        action: () => navigate("/me/pins"),
      },
      {
        id: "go-watching",
        label: "Open Watching",
        group: "My queue",
        action: () => navigate("/me/watching"),
      },
      {
        id: "go-members",
        label: "Manage members",
        hint: "Add member, promote / demote",
        group: "Admin",
        action: () => navigate("/admin/members"),
        adminOnly: true,
      },
      {
        id: "sign-out",
        label: "Sign out",
        group: "Account",
        action: () => {
          logout();
          navigate("/login");
        },
      },
    ];
    return all.filter((c) => !c.adminOnly || isAdmin);
  }, [navigate, logout, isAdmin]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter(
      (c) =>
        c.label.toLowerCase().includes(q) ||
        (c.hint?.toLowerCase().includes(q) ?? false) ||
        c.group.toLowerCase().includes(q),
    );
  }, [commands, query]);

  // Group filtered list while preserving the user-visible order.
  const groups = useMemo(() => {
    const order: Command["group"][] = [
      "Investigation",
      "My queue",
      "Admin",
      "Account",
    ];
    const buckets = new Map<Command["group"], Command[]>();
    for (const c of filtered) {
      if (!buckets.has(c.group)) buckets.set(c.group, []);
      buckets.get(c.group)!.push(c);
    }
    return order
      .filter((g) => buckets.has(g))
      .map((g) => [g, buckets.get(g)!] as const);
  }, [filtered]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
      // give the dialog a tick to mount before focusing the input
      const id = window.setTimeout(() => inputRef.current?.focus(), 0);
      return () => window.clearTimeout(id);
    }
    return undefined;
  }, [open]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  function runAt(index: number) {
    const c = filtered[index];
    if (!c) return;
    c.action();
    onClose();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      runAt(activeIndex);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  }

  if (!open) return null;

  let runningIndex = -1;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[12vh]"
    >
      <div
        className="absolute inset-0 bg-ink/70 backdrop-blur-sm animate-fade-in"
        onClick={onClose}
        aria-hidden
      />
      <div className="relative w-full max-w-xl overflow-hidden rounded-lg border border-ink-line bg-ink-elevated shadow-panel animate-fade-in">
        <div className="flex items-center gap-2 border-b border-ink-line px-3 py-2.5">
          <svg
            viewBox="0 0 16 16"
            fill="none"
            className="h-4 w-4 text-fg-muted"
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
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search a command…"
            className="flex-1 bg-transparent text-sm text-fg placeholder:text-fg-dim focus:outline-none"
          />
          <span className="rk-kbd">esc</span>
        </div>

        <div className="max-h-[50vh] overflow-y-auto py-1">
          {groups.length === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-fg-muted">
              No matches.
            </div>
          ) : null}
          {groups.map(([group, items]) => (
            <div key={group} className="py-1">
              <div className="px-3 pb-1 pt-2 text-2xs font-medium uppercase tracking-[0.2em] text-fg-dim">
                {group}
              </div>
              <ul role="listbox">
                {items.map((c) => {
                  runningIndex += 1;
                  const idx = runningIndex;
                  const active = idx === activeIndex;
                  return (
                    <li
                      key={c.id}
                      role="option"
                      aria-selected={active}
                      onMouseEnter={() => setActiveIndex(idx)}
                      onClick={() => runAt(idx)}
                      className={[
                        "flex cursor-pointer items-center gap-3 px-3 py-2 text-sm",
                        active
                          ? "bg-ink-subtle text-fg shadow-[inset_2px_0_0_0_theme(colors.accent.DEFAULT)]"
                          : "text-fg-muted hover:bg-ink-subtle/60",
                      ].join(" ")}
                    >
                      <span className="flex-1 truncate">{c.label}</span>
                      {c.hint ? (
                        <span className="truncate text-xs text-fg-dim">
                          {c.hint}
                        </span>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>

        <div className="flex items-center justify-between border-t border-ink-line bg-ink-panel/60 px-3 py-1.5 text-2xs uppercase tracking-[0.18em] text-fg-dim">
          <div className="flex items-center gap-2">
            <span className="rk-kbd">↑</span>
            <span className="rk-kbd">↓</span>
            <span>navigate</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="rk-kbd">⏎</span>
            <span>open</span>
          </div>
        </div>
      </div>
    </div>
  );
}
