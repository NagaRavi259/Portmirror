/** @type {import('tailwindcss').Config} */
// Colors below resolve through CSS custom properties (defined for both themes in index.css),
// not literal hex, so a single dark-mode stylesheet swap re-themes every existing utility class
// across the whole app - no per-component "dark:" variants needed. `rgb(var(--x) / <alpha-value>)`
// is Tailwind's documented pattern for keeping opacity modifiers (`bg-white/85` etc, used
// throughout this app) working with variable-based colors; each variable holds space-separated
// "R G B" channels, not a hex string, for exactly that reason.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
const scale = (name) => ({
  50: v(`${name}-50`), 100: v(`${name}-100`), 200: v(`${name}-200`), 300: v(`${name}-300`), 400: v(`${name}-400`),
  500: v(`${name}-500`), 600: v(`${name}-600`), 700: v(`${name}-700`), 800: v(`${name}-800`), 900: v(`${name}-900`),
});

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Inter Variable"', "Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      colors: {
        // "white" is repurposed as the theme's card/panel surface color (what the vast majority of
        // existing bg-white/ring-white/border-white usage actually means) since it's never literally
        // #fff in dark mode. Text that must stay white regardless of theme (on a vivid button or
        // badge, which doesn't itself change with the theme) uses the new `oncolor` token instead.
        white: v("surface"),
        oncolor: "#ffffff",
        ink: { 950: v("ink-950"), 900: v("ink-900"), 700: v("ink-700"), 500: v("ink-500"), 400: v("ink-400"), 300: v("ink-300") },
        line: { DEFAULT: v("line"), strong: v("line-strong") },
        canvas: v("canvas"),
        accent: { 50: v("accent-50"), 100: v("accent-100"), 400: v("accent-400"), 500: v("accent-500"), 600: v("accent-600") },
        signal: { 50: v("signal-50"), 400: v("signal-400"), 500: v("signal-500"), 600: v("signal-600") },
        rose: scale("rose"),
        emerald: scale("emerald"),
        amber: scale("amber"),
      },
      boxShadow: {
        card: "0 1px 2px rgba(16,24,48,.04), 0 8px 24px -12px rgba(16,24,48,.10)",
        lift: "0 2px 4px rgba(16,24,48,.06), 0 18px 40px -16px rgba(40,48,120,.25)",
        glow: "0 0 0 4px rgba(79,70,229,.12)",
      },
      borderRadius: { xl2: "1.125rem" },
      keyframes: {
        pulse2: { "0%,100%": { opacity: 1, transform: "scale(1)" }, "50%": { opacity: 0.45, transform: "scale(.85)" } },
        slidein: { from: { transform: "translateX(24px)", opacity: 0 }, to: { transform: "none", opacity: 1 } },
        fadeup: { from: { transform: "translateY(6px)", opacity: 0 }, to: { transform: "none", opacity: 1 } },
      },
      animation: {
        pulse2: "pulse2 1.8s ease-in-out infinite",
        slidein: "slidein .22s cubic-bezier(.2,.8,.2,1)",
        fadeup: "fadeup .25s ease-out both",
      },
    },
  },
  plugins: [],
};
