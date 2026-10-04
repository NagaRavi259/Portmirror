import { ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Api, TlsStatus } from "../lib/api";
import { useToast } from "./Toasts";

/** Shown on every page while an HTTPS change is waiting for confirmation. It counts down, and if nobody
 * confirms before it reaches zero, the gateway reverts the change by itself. */
export function TlsBanner() {
  const toast = useToast();
  const [st, setSt] = useState<TlsStatus | null>(null);
  const [left, setLeft] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = () => Api.tls().then((s) => { if (alive) { setSt(s); setLeft(s.seconds_left); } }).catch(() => {});
    load();
    const id = setInterval(load, 2000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  useEffect(() => {
    if (!st?.pending) return;
    const id = setInterval(() => setLeft((n) => Math.max(0, n - 1)), 1000);
    return () => clearInterval(id);
  }, [st?.pending]);
  if (!st?.pending) return null;
  const keep = async () => { await Api.tlsConfirm().catch(() => {}); setSt(null); toast("ok", "Kept. The change stays."); };
  const revert = async () => { await Api.tlsRevert().catch(() => {}); setSt(null); toast("ok", "Reverting to the previous setting…"); };
  return (
    <div role="alert" className="mb-6 flex flex-wrap items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      <ShieldAlert size={18} className="shrink-0" />
      <span className="flex-1">
        <b>Keep the HTTPS change?</b> It reverts in <span className="font-mono tabular-nums">{left}s</span> unless you confirm.
      </span>
      <button className="btn-primary h-8 px-3 text-xs" onClick={keep}>Keep this change</button>
      <button className="btn-ghost h-8 px-3 text-xs" onClick={revert}>Revert now</button>
    </div>
  );
}
