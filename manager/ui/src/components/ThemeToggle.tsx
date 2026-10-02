import { Moon, Sun } from "lucide-react";
import { setTheme, useTheme } from "../lib/theme";

export function ThemeToggle() {
  const theme = useTheme();
  const dark = theme === "dark";
  return (
    <button type="button" onClick={() => setTheme(dark ? "light" : "dark")}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      title={dark ? "Switch to light theme" : "Switch to dark theme"}
      className="grid h-8 w-8 place-items-center rounded-full text-ink-500 hover:bg-ink-950/[.05] hover:text-ink-900">
      {dark ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  );
}
