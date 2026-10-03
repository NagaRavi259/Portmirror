import { ChevronRight, Download, ScrollText } from "lucide-react";
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
      <div className="flex justify-end">
        <a className="btn-outline" href="/api/export/audit.csv" download><Download size={15} /> Export CSV</a>
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
                  {open === r.id && <AuditDetail detail={r.detail} deviceNames={deviceNames} />}
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

/** Changed fields only, old value -> new value, for entries that recorded a before/after pair.
 * Anything else (creates, deletes, actions with no pair) returns null and shows its raw detail. */
export function changedFields(detail: unknown): { key: string; before: unknown; after: unknown }[] | null {
  if (!detail || typeof detail !== "object") return null;
  const d = detail as Record<string, unknown>;
  const before = d.before as Record<string, unknown> | undefined;
  const after = d.after as Record<string, unknown> | undefined;
  if (!before || !after || typeof before !== "object" || typeof after !== "object") return null;
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])].sort();
  return keys
    .filter((k) => JSON.stringify(before[k]) !== JSON.stringify(after[k]))
    .map((k) => ({ key: k, before: before[k], after: after[k] }));
}

function show(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function AuditDetail({ detail, deviceNames }: { detail: unknown; deviceNames: Record<string, string> }) {
  const [raw, setRaw] = useState(false);
  const diff = changedFields(detail);
  const rawText = detail ? withDeviceNames(JSON.stringify(detail, null, 2), deviceNames) : "no details";
  return (
    <div className="mx-5 mb-3 rounded-lg border border-line bg-canvas p-3">
      {diff && !raw ? (
        diff.length === 0 ? <p className="text-sm text-ink-500">Nothing changed.</p> : (
          <table className="w-full text-[12.5px]">
            <tbody>
              {diff.map((c) => (
                <tr key={c.key} className="border-b border-line/70 last:border-0">
                  <td className="w-44 py-1.5 pr-3 font-mono text-ink-500">{c.key}</td>
                  <td className="py-1.5 pr-3 font-mono text-rose-600 line-through decoration-rose-300">{withDeviceNames(show(c.before), deviceNames)}</td>
                  <td className="py-1.5 font-mono font-medium text-emerald-700">{withDeviceNames(show(c.after), deviceNames)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      ) : (
        <pre className="max-h-72 overflow-auto font-mono text-[11.5px] leading-5 text-ink-700 scroll-thin">{rawText}</pre>
      )}
      {diff && (
        <button className="mt-2 text-xs font-medium text-accent-600 hover:underline" onClick={() => setRaw((v) => !v)}>
          {raw ? "Show changes only" : "Show raw JSON"}
        </button>
      )}
    </div>
  );
}
