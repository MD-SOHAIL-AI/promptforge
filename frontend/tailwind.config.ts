import type { Config } from "tailwindcss";
import tailwindcssAnimate from "tailwindcss-animate";

const config: Config = {
  darkMode: ["class"],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./hooks/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        border: "var(--fx-border)",
        input: "var(--fx-input)",
        ring: "var(--fx-accent)",
        background: "var(--fx-bg)",
        foreground: "var(--fx-text)",
        primary: {
          DEFAULT: "var(--fx-accent)",
          foreground: "var(--fx-accent-contrast)",
        },
        secondary: {
          DEFAULT: "var(--fx-panel-elevated)",
          foreground: "var(--fx-text)",
        },
        muted: {
          DEFAULT: "var(--fx-hover)",
          foreground: "var(--fx-text-muted)",
        },
        accent: {
          DEFAULT: "var(--fx-accent-soft)",
          foreground: "var(--fx-text)",
        },
        destructive: {
          DEFAULT: "var(--fx-error)",
          foreground: "#ffffff",
        },
        success: {
          DEFAULT: "var(--fx-success)",
          soft: "var(--fx-success-soft)",
          fg: "var(--fx-success)",
        },
        warning: {
          DEFAULT: "var(--fx-warning)",
          soft: "var(--fx-warning-soft)",
          fg: "var(--fx-warning)",
        },
        info: {
          DEFAULT: "var(--fx-info)",
          soft: "var(--fx-info-soft)",
          fg: "var(--fx-info)",
        },
        error: {
          DEFAULT: "var(--fx-error)",
          soft: "var(--fx-error-soft)",
          fg: "var(--fx-error)",
        },
        danger: {
          DEFAULT: "var(--fx-danger)",
          soft: "var(--fx-danger-soft)",
          fg: "var(--fx-danger)",
        },
        card: {
          DEFAULT: "var(--fx-panel-elevated)",
          foreground: "var(--fx-text)",
        },
        popover: {
          DEFAULT: "var(--fx-panel-elevated)",
          foreground: "var(--fx-text)",
        },
      },
      borderRadius: {
        DEFAULT: "var(--fx-radius-sm)",
        lg: "var(--fx-radius)",
        xl: "var(--fx-radius-lg)",
      },
      fontFamily: {
        sans: ["Inter", "Segoe UI Variable", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "Cascadia Code", "SFMono-Regular", "Consolas", "monospace"],
      },
      fontSize: {
        micro: ["var(--fx-text-2xs)", { lineHeight: "14px" }],
        caption: ["var(--fx-text-xs)", { lineHeight: "16px" }],
        body: ["var(--fx-text-sm)", { lineHeight: "20px" }],
        lead: ["var(--fx-text-lg)", { lineHeight: "24px" }],
      },
      boxShadow: {
        "fx-2xs": "var(--fx-shadow-2xs)",
        "fx-xs": "var(--fx-shadow-xs)",
        "fx-sm": "var(--fx-shadow-sm)",
        "fx-md": "var(--fx-shadow-md)",
        "fx-lg": "var(--fx-shadow-lg)",
        "fx-xl": "var(--fx-shadow-xl)",
        "fx-2xl": "var(--fx-shadow-2xl)",
      },
      keyframes: {
        pulseBar: {
          "0%, 100%": { opacity: "0.35" },
          "50%": { opacity: "1" },
        },
      },
      animation: {
        "pulse-bar": "pulseBar 1.2s ease-in-out infinite",
      },
    },
  },
  plugins: [tailwindcssAnimate],
};

export default config;
