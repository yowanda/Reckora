import { useEffect, useState } from "react";

/**
 * Square avatar tile rendered on the left rail of every {@link TraceCard}.
 *
 * Falls back to a monogram on missing / failed images so a card never
 * collapses to an empty box. Sizing is responsive — 56px on mobile
 * (matching the Maigret-style stacked layout) up to 80px on `sm:`+ so
 * the avatar reads as a deliberate primary anchor next to the field
 * table.
 */
export function Avatar({
  src,
  fallback,
  alt,
}: {
  src?: string | null;
  fallback: string;
  alt?: string;
}) {
  const [errored, setErrored] = useState(false);
  const trimmed = typeof src === "string" ? src.trim() : "";
  const showImage = trimmed.length > 0 && !errored;

  // If a card mounts with one URL and re-renders with another (e.g. a
  // lazily-loaded trace), reset the error gate so the new image gets a
  // fresh attempt.
  useEffect(() => {
    setErrored(false);
  }, [trimmed]);

  return (
    <div
      className="flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-md border border-ink-line bg-ink-subtle text-base font-semibold uppercase tracking-wider text-fg-muted shadow-panel sm:h-20 sm:w-20 sm:text-xl"
      aria-hidden={showImage ? undefined : true}
    >
      {showImage ? (
        <img
          src={trimmed}
          alt={alt ?? ""}
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setErrored(true)}
          className="h-full w-full object-cover"
        />
      ) : (
        <span className="font-mono text-fg-muted">{monogram(fallback)}</span>
      )}
    </div>
  );
}

function monogram(value: string): string {
  const cleaned = value.trim();
  if (cleaned.length === 0) {
    return "?";
  }
  const ascii = cleaned.replace(/[^A-Za-z0-9]/g, "");
  if (ascii.length === 0) {
    return cleaned.charAt(0).toUpperCase();
  }
  return ascii.charAt(0).toUpperCase();
}
