import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <section className="mx-auto max-w-md py-20 text-center">
      <div className="font-mono text-5xl font-semibold tracking-[0.18em] text-fg">
        404
      </div>
      <div className="mt-2 text-2xs uppercase tracking-[0.3em] text-fg-dim">
        signal lost
      </div>
      <p className="mt-4 text-sm leading-relaxed text-fg-muted">
        The route you tried to open does not exist on this Reckora deployment.
      </p>
      <Link
        to="/subjects"
        className="mt-6 inline-flex items-center gap-2 rounded border border-ink-line bg-ink-panel px-3 py-1.5 text-sm text-fg-muted transition-colors hover:border-accent/40 hover:text-fg"
      >
        <svg viewBox="0 0 16 16" fill="none" className="h-3.5 w-3.5" aria-hidden>
          <path
            d="M11 3 7 8l4 5"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        Back to subjects
      </Link>
    </section>
  );
}
