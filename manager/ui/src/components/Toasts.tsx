import { CheckCircle2, AlertTriangle, Info } from "lucide-react";
import { createContext, ReactNode, useCallback, useContext, useState } from "react";
import { cx } from "./ui";

type Kind = "ok" | "error" | "info";
interface Toast { id: number; kind: Kind; text: string }

const Ctx = createContext<(kind: Kind, text: string) => void>(() => {});
export const useToast = () => useContext(Ctx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((kind: Kind, text: string) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, kind, text }]);
    window.setTimeout(() => setItems((xs) => xs.filter((t) => t.id !== id)), kind === "error" ? 7000 : 3500);
  }, []);
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-[360px] max-w-[calc(100vw-2.5rem)] flex-col gap-2" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} role={t.kind === "error" ? "alert" : "status"}
            className={cx("pointer-events-auto flex items-start gap-2.5 rounded-xl border bg-white px-4 py-3 text-sm shadow-lift animate-fadeup",
              t.kind === "error" ? "border-rose-200" : "border-line")}>
            {t.kind === "ok" ? <CheckCircle2 size={18} className="mt-px shrink-0 text-emerald-500" />
              : t.kind === "error" ? <AlertTriangle size={18} className="mt-px shrink-0 text-rose-500" />
                : <Info size={18} className="mt-px shrink-0 text-accent-500" />}
            <span className="text-ink-700">{t.text}</span>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
