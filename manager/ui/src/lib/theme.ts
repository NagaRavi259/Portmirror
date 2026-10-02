import { useEffect, useState } from "react";

export type Theme = "light" | "dark";
const KEY = "pm-theme";
export const THEME_CHANGE_EVENT = "pm-theme-change";

function systemPrefersDark(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

/** The user's explicit choice, or null if they've never overridden the system setting. */
export function storedTheme(): Theme | null {
  const v = localStorage.getItem(KEY);
  return v === "light" || v === "dark" ? v : null;
}

export function resolveTheme(): Theme {
  return storedTheme() ?? (systemPrefersDark() ? "dark" : "light");
}

function apply(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
}

/** Sets an explicit theme, or null to go back to following the system setting. */
export function setTheme(theme: Theme | null) {
  if (theme) localStorage.setItem(KEY, theme);
  else localStorage.removeItem(KEY);
  apply(resolveTheme());
  window.dispatchEvent(new Event(THEME_CHANGE_EVENT));
}

/** Applies the right theme immediately on load (before React mounts, to avoid a flash of the
 * wrong one) and keeps it in sync with the OS setting for as long as the user hasn't overridden it. */
export function initTheme() {
  apply(resolveTheme());
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!storedTheme()) {
      apply(resolveTheme());
      window.dispatchEvent(new Event(THEME_CHANGE_EVENT));
    }
  });
}

/** Re-renders whenever the theme changes - for the rare case (canvas-drawn charts) that CSS
 * alone can't re-theme on its own. */
export function useTheme(): Theme {
  const [theme, setLocal] = useState(resolveTheme);
  useEffect(() => {
    const on = () => setLocal(resolveTheme());
    window.addEventListener(THEME_CHANGE_EVENT, on);
    return () => window.removeEventListener(THEME_CHANGE_EVENT, on);
  }, []);
  return theme;
}
