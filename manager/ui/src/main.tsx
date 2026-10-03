import "./lib/locale-guard";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ToastProvider } from "./components/Toasts";
import { initTheme } from "./lib/theme";
import "./index.css";

initTheme();   // before the first paint, so there's no flash of the wrong theme

// Service workers only run on https or localhost, so this is a no-op on a plain-http LAN address
// and only does anything when the dashboard is reached over a secure origin (e.g. a Tailscale HTTPS URL).
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </StrictMode>,
);
