import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { Breadcrumbs } from "./Breadcrumbs";
import { CommandPalette } from "./CommandPalette";
import { Sidebar } from "./Sidebar";

export function Layout() {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const location = useLocation();

  // ⌘K / Ctrl+K opens the command palette anywhere in the app, except
  // while the user is mid-typing in a multi-line text area (so we do
  // not eat their keystroke when they happen to type "k").
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Close the mobile drawer on every route change.
  useEffect(() => {
    function onResize() {
      if (window.innerWidth >= 1024) setDrawerOpen(false);
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return (
    <div className="flex min-h-screen">
      {/* Desktop sidebar */}
      <div className="hidden lg:flex">
        <Sidebar onOpenPalette={() => setPaletteOpen(true)} />
      </div>

      {/* Mobile drawer */}
      {drawerOpen ? (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div
            className="absolute inset-0 bg-ink/70 backdrop-blur-sm"
            onClick={() => setDrawerOpen(false)}
            aria-hidden
          />
          <div className="relative h-full w-60">
            <Sidebar
              onClose={() => setDrawerOpen(false)}
              onOpenPalette={() => {
                setDrawerOpen(false);
                setPaletteOpen(true);
              }}
            />
          </div>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 border-b border-ink-line bg-ink/85 backdrop-blur">
          <div className="flex items-center gap-3 px-4 py-2.5 lg:px-6">
            <button
              type="button"
              onClick={() => setDrawerOpen(true)}
              className="-ml-1 inline-flex h-8 w-8 items-center justify-center rounded text-fg-muted transition-colors hover:bg-ink-subtle hover:text-fg lg:hidden"
              aria-label="Open navigation"
            >
              <svg viewBox="0 0 16 16" fill="none" className="h-4 w-4" aria-hidden>
                <path
                  d="M2 4h12M2 8h12M2 12h12"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinecap="round"
                />
              </svg>
            </button>
            <div className="min-w-0 flex-1">
              <Breadcrumbs />
            </div>
            <button
              type="button"
              onClick={() => setPaletteOpen(true)}
              className="hidden items-center gap-2 rounded border border-ink-line bg-ink-panel px-2.5 py-1 text-xs text-fg-muted transition-colors hover:border-accent/40 hover:text-fg sm:inline-flex"
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
              <span>Search</span>
              <span className="rk-kbd">⌘K</span>
            </button>
          </div>
        </header>

        <main
          key={location.pathname}
          className="rk-page mx-auto w-full max-w-6xl flex-1 px-4 py-6 lg:px-6"
        >
          <Outlet />
        </main>
      </div>

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
      />
    </div>
  );
}
