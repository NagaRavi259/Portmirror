import { ChevronRight, ScrollText } from "lucide-react";
import { useEffect, useState } from "react";
import { Card, Empty, Spinner, cx } from "../components/ui";
import { Api, AuditEntry } from "../lib/api";
import { datetime, withDeviceNames } from "../lib/format";

const TONE: Record<string, string> = {
  create: "bg-emerald-50 text-emerald-700", enable: "bg-emerald-50 text-emerald-700", import: "bg-emerald-50 text-emerald-700",
  seed: "bg-emerald-50 text-emerald-700", update: "bg-accent-50 text-accent-600", delete: "bg-rose-50 text-rose-700",
  disable: "bg-amber-50 text-amber-700", expire: "bg-amber-50 text-amber-700", kill: "bg-rose-50 text-rose-700",
  kill_all: "bg-rose-50 text-rose-700", login_failed: "bg-rose-50 text-rose-700", drift_repaired: "bg-amber-50 text-amber-700",
};

export function Audit({ deviceNames }: { deviceNames: Record<string, string> }) {
  const [rows, setRows] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(true);
  const [open, setOpen] = useState<number | null>(null);
  const [filter, setFilter] = useState("all");

  const load = async (before?: number) => {
    setLoading(true);
    const page = await Api.audit(100, before);
    setRows((r) => (before ? [...r, ...page] : page));
    setMore(page.length === 100);
    setLoading(false);
  };
  useEffect(() => { load(); }, []);

  const groups = ["all", "forward", "connections", "auth", "token", "kernel", "manager"];
  const shown = filter === "all" ? rows : rows.filter((r) => r.action.startsWith(filter));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-ink-950">Audit log</h1>
        <p className="mt-1 text-sm text-ink-500">Every change to the gateway — who, what and when.</p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {groups.map((g) => (
          <button key={g} onClick={() => setFilter(g)}
            className={cx("h-8 rounded-full px-3 text-xs font-semibold capitalize transition",
              filter === g ? "bg-accent-500 text-oncolor" : "bg-white text-ink-500 ring-1 ring-line hover:text-ink-900")}>{g}</button>
        ))}
      </div>
      <Card className="overflow-hidden">
        {!loading && shown.length === 0 ? <Empty icon={<ScrollText size={20} />} title="Nothing here yet" /> : (
          <ul className="divide-y divide-line">
            {shown.map((r) => {
              const [grp, act] = r.action.split(".");
              return (
                <li key={r.id}>
                  <button className="flex w-full items-center gap-4 px-5 py-3 text-left hover:bg-canvas/60" onClick={() => setOpen(open === r.id ? null : r.id)}
                    aria-expanded={open === r.id}>
                    <ChevronRight size={14} className={cx("shrink-0 text-ink-300 transition-transform", open === r.id && "rotate-90")} />
                    <span className="num w-40 shrink-0 text-xs text-ink-400">{datetime(r.ts)}</span>
                    <span className="w-24 shrink-0 text-xs font-medium uppercase tracking-wider text-ink-400">{grp}</span>
                    <span className={cx("chip shrink-0", TONE[act] ?? "bg-canvas text-ink-500 ring-1 ring-line")}>{act ?? grp}</span>
                    <span className="min-w-0 flex-1 truncate text-sm text-ink-900">{withDeviceNames(r.target, deviceNames)}</span>
                    <span className="shrink-0 font-mono text-xs text-ink-500">{r.actor}</span>
                  </button>
                  {open === r.id && (
                    <pre className="mx-5 mb-3 max-h-72 overflow-auto rounded-lg border border-line bg-canvas p-3 font-mono text-[11.5px] leading-5 text-ink-700 scroll-thin">
                      {r.detail ? withDeviceNames(JSON.stringify(r.detail, null, 2), deviceNames) : "no details"}
                    </pre>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        {more && (
          <div className="border-t border-line p-3 text-center">
            <button className="btn-ghost" disabled={loading} onClick={() => load(rows[rows.length - 1]?.id)}>
              {loading ? <Spinner /> : "Load older entries"}
            </button>
          </div>
        )}
      </Card>
    </div>
  );
}
