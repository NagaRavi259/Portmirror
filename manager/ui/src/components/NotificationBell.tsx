import { AlertTriangle, Bell, Check, Info, VolumeX, X, XCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Api, Notification } from "../lib/api";
import { ago } from "../lib/format";
import { useToast } from "./Toasts";
import { cx, Empty, Spinner } from "./ui";

const ICON = { info: Info, warning: AlertTriangle, error: XCircle } as const;
const TONE = {
  info: "text-accent-500 bg-accent-50", warning: "text-amber-600 bg-amber-50", error: "text-rose-600 bg-rose-50",
} as const;

export function NotificationBell() {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const [items, setItems] = useState<Notification[] | null>(null);
  const [muted, setMuted] = useState<string[]>([]);
  const ref = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await Api.notifications();
      setUnread(r.unread);
      setItems(r.items);
      setMuted(r.muted);
    } catch { /* quiet - the header badge just stays as it was */ }
  }, []);

  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, 20000);
    return () => window.clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onClick); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); await refresh(); } catch (e) { toast("error", (e as Error).message); }
  };

  const toggleOpen = () => {
    setOpen((v) => !v);
    if (!open) refresh();
  };

  return (
    <div className="relative" ref={ref}>
      <button type="button" onClick={toggleOpen} aria-label="Notifications"
        className="relative grid h-8 w-8 place-items-center rounded-full text-ink-500 hover:bg-ink-950/[.05] hover:text-ink-900">
        <Bell size={16} />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold text-white">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-10 z-40 w-96 max-w-[90vw] overflow-hidden rounded-xl border border-line bg-white shadow-lift">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <span className="text-sm font-semibold text-ink-900">Notifications</span>
            {unread > 0 && (
              <button className="text-xs font-medium text-accent-600 hover:underline"
                onClick={() => act(() => Api.markAllNotificationsRead())}>Mark all read</button>
            )}
          </div>
          <div className="max-h-96 overflow-y-auto scroll-thin">
            {!items ? (
              <div className="grid h-24 place-items-center"><Spinner className="h-4 w-4 text-accent-500" /></div>
            ) : items.length === 0 ? (
              <Empty icon={<Bell size={18} />} title="Nothing here">You're all caught up.</Empty>
            ) : (
              <ul className="divide-y divide-line">
                {items.map((n) => {
                  const Icon = ICON[n.severity];
                  const isMuted = muted.includes(n.type);
                  const active = n.state === "unread" || n.state === "read";
                  return (
                    <li key={n.id} className={cx("px-4 py-3", n.state === "unread" && "bg-accent-50/30")}>
                      <div className="flex items-start gap-2.5">
                        <span className={cx("mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full", TONE[n.severity])}>
                          <Icon size={13} />
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="text-[13px] leading-snug text-ink-900">{n.message}</p>
                          <p className="mt-0.5 text-[11px] text-ink-400">
                            {ago(n.created_at)}{n.state !== "unread" && n.state !== "read" && ` · ${n.state}`}
                          </p>
                        </div>
                      </div>
                      {active && (
                        <div className="mt-2 flex flex-wrap gap-1.5 pl-[34px]">
                          <button className="chip bg-canvas text-ink-600 ring-1 ring-line hover:text-ink-900"
                            onClick={() => act(() => Api.setNotificationState(n.id, "actioned"))}>
                            <Check size={11} /> Took action
                          </button>
                          <button className="chip bg-canvas text-ink-600 ring-1 ring-line hover:text-ink-900"
                            onClick={() => act(() => Api.setNotificationState(n.id, "dismissed"))}>
                            <X size={11} /> Dismiss
                          </button>
                          {!isMuted && (
                            <button className="chip bg-canvas text-ink-600 ring-1 ring-line hover:text-ink-900"
                              onClick={() => act(async () => { await Api.muteNotificationType(n.type); await Api.setNotificationState(n.id, "dismissed"); })}>
                              <VolumeX size={11} /> Skip future
                            </button>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
