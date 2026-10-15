import type { Config } from "tailwindcss";

/**
 * Tokens, not raw hex, throughout the components. The values live in
 * globals.css as custom properties so the palette can be swapped in one place
 * and so the data-viz validator has a single set of colours to check.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        plane: "var(--plane)",
        surface: "var(--surface-1)",
        "surface-2": "var(--surface-2)",
        "surface-3": "var(--surface-3)",
        ink: "var(--text-primary)",
        "ink-2": "var(--text-secondary)",
        muted: "var(--text-muted)",
        grid: "var(--gridline)",
        baseline: "var(--baseline)",
        hairline: "var(--border)",
        "hairline-strong": "var(--border-strong)",
        accent: "var(--accent)",
        "state-good": "var(--state-good)",
        "state-warn": "var(--state-warn)",
        "state-crit": "var(--state-crit)",
        "accent-soft": "var(--accent-soft)",
        "seq-400": "var(--seq-400)",
        "seq-500": "var(--seq-500)",
        "seq-600": "var(--seq-600)",
        "tier-hypothesis": "var(--tier-hypothesis)",
        "tier-computed": "var(--tier-computed)",
        "tier-measured": "var(--tier-measured)",
      },
      fontFamily: {
        sans: ["system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["ui-monospace", "Cascadia Code", "Consolas", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
    },
  },
  plugins: [],
};

export default config;
