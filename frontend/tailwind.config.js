/** @type {import('tailwindcss').Config} */

// Colours are CSS variables (see src/index.css) so dark mode is a variable swap, not a
// `dark:` variant on every element. Each variable holds space-separated RGB channels.
const channel = (name) => `rgb(var(--${name}) / <alpha-value>)`;
const inkSteps = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter Variable",
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      colors: {
        ink: Object.fromEntries(inkSteps.map((s) => [s, channel(`ink-${s}`)])),
        surface: channel("surface"),
        canvas: channel("canvas"),
        accent: {
          DEFAULT: channel("accent"),
          fg: channel("accent-fg"),
          text: channel("accent-text"),
        },
      },
      animation: {
        "pulse-soft": "pulse-soft 1.6s ease-in-out infinite",
      },
      keyframes: {
        "pulse-soft": {
          "0%, 100%": { opacity: "0.55" },
          "50%": { opacity: "1" },
        },
      },
    },
  },
  plugins: [],
};
