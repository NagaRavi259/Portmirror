// Some browsers under a POSIX locale report navigator.language as e.g. "en-US@posix",
// which Intl rejects - and uPlot builds an Intl.NumberFormat from it at import time.
// Runs before any other module (first import in main.tsx).
try {
  new Intl.NumberFormat(navigator.language);
} catch {
  Object.defineProperty(navigator, "language", { get: () => "en-US" });
}
export {};
