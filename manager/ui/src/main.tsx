import "./lib/locale-guard";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ToastProvider } from "./components/Toasts";
import { initTheme } from "./lib/theme";
import "./index.css";

initTheme();   // before the first paint, so there's no flash of the wrong theme

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </StrictMode>,
);
