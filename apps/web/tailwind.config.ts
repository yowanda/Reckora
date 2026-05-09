import type { Config } from "tailwindcss";

/**
 * Reckora — Tier 3 "Forensic / Intelligence" design tokens.
 *
 * Visual language: deep ink + crisp data, restrained typography, two
 * accents — cyan for live data and primary action, amber for risk and
 * flagged state. Tokens are intentionally semantic: layers (`ink`,
 * `panel`, `subtle`) describe surfaces; `fg` / `fg-muted` describe text;
 * `accent` / `alert` / `ok` / `danger` describe meaning. Components
 * should read against these names rather than against a raw hex.
 *
 * Backward-compat aliases (`bg`, `border.DEFAULT`, `accent.muted`) are
 * preserved so the existing component tree keeps rendering while M5
 * migrates pages onto the new vocabulary.
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // ── Surfaces ────────────────────────────────────────────
        ink: {
          DEFAULT: "#070a12", // page background
          panel: "#0e1320", // cards, tables, sidebar
          subtle: "#161d2e", // hover / focus surface
          elevated: "#1c243a", // popovers, modals
          line: "#1f2738", // default border
          line2: "#2d3a5a", // active border
        },
        // ── Text ────────────────────────────────────────────────
        fg: {
          DEFAULT: "#e6ecf5",
          muted: "#9aa6bd",
          dim: "#5b6885",
        },
        // ── Action / data accent (cyan) ─────────────────────────
        accent: {
          DEFAULT: "#38bdf8",
          strong: "#0ea5e9",
          muted: "#1e3a8a", // dark navy for filled buttons in dark mode
          soft: "rgba(56, 189, 248, 0.12)",
          ring: "rgba(56, 189, 248, 0.45)",
        },
        // ── Risk / alert (amber) ────────────────────────────────
        alert: {
          DEFAULT: "#f59e0b",
          strong: "#d97706",
          soft: "rgba(245, 158, 11, 0.14)",
        },
        // ── Resolved / verified (emerald) ───────────────────────
        ok: {
          DEFAULT: "#34d399",
          strong: "#10b981",
          soft: "rgba(52, 211, 153, 0.14)",
        },
        // ── Failure / destructive (red) ─────────────────────────
        danger: {
          DEFAULT: "#f87171",
          strong: "#ef4444",
          soft: "rgba(248, 113, 113, 0.14)",
        },
        // ── Backward-compat aliases ─────────────────────────────
        // M5 will gradually replace these with the semantic names.
        bg: {
          DEFAULT: "#070a12",
          panel: "#0e1320",
          subtle: "#161d2e",
          elevated: "#1c243a",
        },
        border: {
          DEFAULT: "#1f2738",
          strong: "#2d3a5a",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      fontSize: {
        // Tighter analytical scale.
        "2xs": ["10.5px", { lineHeight: "1.45", letterSpacing: "0.02em" }],
        xs: ["11.5px", { lineHeight: "1.55" }],
        sm: ["13px", { lineHeight: "1.55" }],
        base: ["14px", { lineHeight: "1.6" }],
        md: ["15px", { lineHeight: "1.55" }],
        lg: ["16.5px", { lineHeight: "1.5" }],
        xl: ["20px", { lineHeight: "1.4", letterSpacing: "-0.01em" }],
        "2xl": ["24px", { lineHeight: "1.3", letterSpacing: "-0.015em" }],
        "3xl": ["30px", { lineHeight: "1.2", letterSpacing: "-0.02em" }],
      },
      letterSpacing: {
        crisp: "-0.005em",
        snug: "-0.015em",
        analytic: "-0.02em",
      },
      borderRadius: {
        DEFAULT: "4px",
        sm: "3px",
        md: "6px",
        lg: "8px",
        xl: "12px",
        "2xl": "16px",
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(56, 189, 248, 0.32), 0 0 28px -6px rgba(56, 189, 248, 0.22)",
        amber:
          "0 0 0 1px rgba(245, 158, 11, 0.32), 0 0 28px -6px rgba(245, 158, 11, 0.22)",
        panel:
          "0 1px 0 rgba(255,255,255,0.02), 0 12px 32px -20px rgba(0,0,0,0.7)",
        ring: "0 0 0 3px rgba(56, 189, 248, 0.25)",
      },
      keyframes: {
        scan: {
          "0%": { transform: "translateX(-100%)" },
          "100%": { transform: "translateX(100%)" },
        },
        "pulse-glow": {
          "0%, 100%": { opacity: "0.55" },
          "50%": { opacity: "1" },
        },
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(2px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        scan: "scan 2.4s ease-in-out infinite",
        "pulse-glow": "pulse-glow 2s ease-in-out infinite",
        "fade-in": "fade-in 0.18s ease-out",
      },
    },
  },
  plugins: [],
} satisfies Config;
