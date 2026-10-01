/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Inter Variable"', "Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      colors: {
        ink: { 950: "#0b1020", 900: "#111831", 700: "#334063", 500: "#5b6787", 400: "#8792ad", 300: "#b4bccf" },
        line: { DEFAULT: "#e6e9f2", strong: "#d5daea" },
        canvas: "#f6f7fb",
        accent: { 50: "#eef0ff", 100: "#e0e4ff", 400: "#6d6cf6", 500: "#4f46e5", 600: "#4338ca" },
        signal: { 50: "#ecfeff", 400: "#22d3ee", 500: "#06b6d4", 600: "#0891b2" },
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
