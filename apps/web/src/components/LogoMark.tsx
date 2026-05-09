interface LogoMarkProps {
  className?: string;
}

/**
 * Reckora monogram — a hexagonal data-glyph: outer hex (perimeter), inner
 * crosshair (focus), and a single vertex highlighted in accent for a
 * subtle "focus point" feel. Pure SVG so it scales cleanly to any size.
 */
export function LogoMark({ className = "h-6 w-6" }: LogoMarkProps) {
  return (
    <svg
      viewBox="0 0 32 32"
      className={className}
      aria-hidden
      focusable="false"
    >
      <defs>
        <linearGradient id="rk-logo-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#38bdf8" />
          <stop offset="100%" stopColor="#0ea5e9" />
        </linearGradient>
      </defs>
      <polygon
        points="16,3 27.5,9.5 27.5,22.5 16,29 4.5,22.5 4.5,9.5"
        fill="none"
        stroke="url(#rk-logo-grad)"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <circle cx="16" cy="16" r="3.5" fill="none" stroke="#38bdf8" strokeWidth="1.4" />
      <path
        d="M16 9.5v3M16 19.5v3M9.5 16h3M19.5 16h3"
        stroke="#9aa6bd"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
      <circle cx="27.5" cy="9.5" r="1.6" fill="#f59e0b" />
    </svg>
  );
}
