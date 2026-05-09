import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: "#0b0d10",
          panel: "#13161b",
          subtle: "#1b1f25",
        },
        border: {
          DEFAULT: "#222831",
        },
        accent: {
          DEFAULT: "#7aa2f7",
          muted: "#3b4a6b",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
