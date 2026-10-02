import { Radio, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ClientLabel } from "../components/ClientLabel";
import { Card, Empty, ProtoBadge, Segmented, Spinner, StatusDot, cx } from "../components/ui";
import { Api, ConnectionLogEntry, ConnectionLogSummaryRow, Forward } from "../lib/api";
import { bytes, datetime, duration, ports } from "../lib/format";

type View = "recent" | "clients" | "forwards";

function forwardLabel(forwards: Forward[], fid: number): string {
  const f = forwards.find((x) => x.id === fid);
  return f ? `${f.name} (:${ports(f.listen_port, f.listen_port_end)})` : `#${fid}`;
}

export function Connections({ forwards, deviceNames, onDeviceNamesChanged }: {
  forwards: Forward[]; deviceNames: Record<string, string>; onDeviceNamesChanged: () => void;
}) {
  const [view, setView] = useState<View>("recent");
  const [rows, setRows] = useState<ConnectionLogEntry[]>([]);
  const [summary, setSummary] = useState<ConnectionLogSummaryRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(true);
  const [forwardFilter, setForwardFilter] = useState<number | "">("");
  const [clientFilter, setClientFilter] = useState("");

  const loadRecent = async (before?: number) => {
    setLoading(true);
    const page = await Api.connectionLog({
      forwardId: forwardFilter === "" ? undefined : forwardFilter,
      clientIp: clientFilter.trim() || undefined,
      before,
    });
    setRows((r) => (before ? [...r, ...page] : page));
    setMore(page.length === 200);
    setLoading(false);
  };

  useEffect(() => {
    if (view !== "recent") return;
    loadRecent();
    const t = window.setInterval(() => loadRecent(), 5000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, forwardFilter, clientFilter]);

  useEffect(() => {
    if (view === "recent") return;
    setLoading(true);
    Api.connectionLogSummary(view === "clients" ? "client" : "forward").then((s) => { setSummary(s); setLoading(false); });
  }, [view]);

  const filteredRows = useMemo(() => rows, [rows]);
  const maxBytes = Math.max(...summary.map((r) => r.bytes_in + r.bytes_out), 1);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink-950">Connections</h1>
          <p className="mt-1 text-sm text-ink-500">
            Real traffic the kernel has observed — who connected, to which forward, when, and how much data moved.
            For administrative changes, see the <a href="#/audit" className="text-accent-600 hover:underline">audit log</a> instead.
          </p>
        </div>
        <Segmented value={view} onChange={setView} options={[
          { value: "recent", label: "Recent sessions" }, { value: "clients", label: "By client" }, { value: "forwards", label: "By forward" },
        ]} />
      </div>

      {view === "recent" && (
        <div className="flex flex-wrap gap-3">
          <select className="input h-9 w-auto" value={forwardFilter}
            onChange={(e) => setForwardFilter(e.target.value === "" ? "" : Number(e.target.value))} aria-label="Filter by forward">
            <option value="">All forwards</option>
            {forwards.map((f) => <option key={f.id} value={f.id}>{f.name} (:{ports(f.listen_port, f.listen_port_end)})</option>)}
          </select>
          <div className="relative w-56">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" />
            <input className="input h-9 pl-8" placeholder="Client IP…" value={clientFilter}
              onChange={(e) => setClientFilter(e.target.value)} aria-label="Filter by client IP" />
          </div>
        </div>
      )}

      <Card className="overflow-hidden">
        {view === "recent" ? (
          !loading && filteredRows.length === 0 ? (
            <Empty icon={<Radio size={20} />} title="No connections recorded yet">
              Sessions appear here within a few seconds of a client using a forward.
            </Empty>
          ) : (
            <>
              <div className="overflow-x-auto scroll-thin">
                <table className="w-full min-w-[820px] text-sm">
                  <thead>
                    <tr className="border-b border-line text-left">
                      {["", "Client", "Forward", "Started", "Duration", "In", "Out"].map((h, i) => (
                        <th key={i} className={cx("label px-5 py-2.5", i >= 5 && "text-right")}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="font-mono text-[12.5px]">
                    {filteredRows.map((r) => {
                      const live = !r.ended_at;
                      const secs = ((r.ended_at ? Date.parse(r.ended_at) : Date.now()) - Date.parse(r.started_at)) / 1000;
                      return (
                        <tr key={r.id} className="border-b border-line/70 last:border-0 hover:bg-canvas/60">
                          <td className="px-5 py-2.5"><StatusDot state={live ? "up" : "off"} pulse={live} /></td>
                          <td className="px-5 py-2.5 text-ink-900">
                            <ClientLabel ip={r.client_ip} deviceNames={deviceNames} onChanged={onDeviceNamesChanged} />
                            <span className="text-ink-400">:{r.client_port}</span>
                          </td>
                          <td className="px-5 py-2.5"><span className="mr-1.5 inline-block align-middle"><ProtoBadge protocol={r.proto} /></span>
                            <span className="font-sans text-ink-700">{forwardLabel(forwards, r.fid)}</span></td>
                          <td className="px-5 py-2.5 text-ink-500">{datetime(r.started_at)}</td>
                          <td className="px-5 py-2.5 text-ink-500">{duration(Math.max(0, secs))}{live && <span className="ml-1.5 chip bg-emerald-50 font-sans text-emerald-700">live</span>}</td>
                          <td className="px-5 py-2.5 text-right text-accent-600">{bytes(r.bytes_in)}</td>
                          <td className="px-5 py-2.5 text-right text-signal-600">{bytes(r.bytes_out)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {more && (
                <div className="border-t border-line p-3 text-center">
                  <button className="btn-ghost" disabled={loading} onClick={() => loadRecent(rows[rows.length - 1]?.id)}>
                    {loading ? <Spinner /> : "Load older sessions"}
                  </button>
                </div>
              )}
            </>
          )
        ) : loading ? (
          <div className="grid h-40 place-items-center"><Spinner className="h-5 w-5 text-accent-500" /></div>
        ) : summary.length === 0 ? (
          <Empty icon={<Radio size={20} />} title="No connections recorded yet" />
        ) : (
          <div className="overflow-x-auto scroll-thin">
            <table className="w-full min-w-[720px] text-sm">
              <thead>
                <tr className="border-b border-line text-left">
                  {[view === "clients" ? "Client" : "Forward", "", "Sessions", "Live now", "Last seen", "Total traffic"].map((h, i) => (
                    <th key={i} className={cx("label px-5 py-2.5", i >= 2 && i <= 3 && "text-right")}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {summary.map((r) => (
                  <tr key={r.key} className="border-b border-line/70 last:border-0">
                    <td className="px-5 py-3 font-mono text-[12.5px] text-ink-900">
                      {view === "clients"
                        ? <ClientLabel ip={r.key} deviceNames={deviceNames} onChanged={onDeviceNamesChanged} />
                        : forwardLabel(forwards, Number(r.key))}
                    </td>
                    <td className="w-32 px-5 py-3">
                      <div className="h-1.5 overflow-hidden rounded-full bg-ink-950/[.05]">
                        <div className="h-full rounded-full bg-gradient-to-r from-accent-500 to-signal-500"
                          style={{ width: `${((r.bytes_in + r.bytes_out) / maxBytes) * 100}%` }} />
                      </div>
                    </td>
                    <td className="num px-5 py-3 text-right text-ink-700">{r.sessions}</td>
                    <td className="num px-5 py-3 text-right">{r.live > 0 ? <span className="text-emerald-600">{r.live}</span> : <span className="text-ink-300">0</span>}</td>
                    <td className="px-5 py-3 text-xs text-ink-400">{datetime(r.last_seen)}</td>
                    <td className="num px-5 py-3 text-right text-ink-900">
                      <span className="text-accent-600">↓{bytes(r.bytes_in)}</span> <span className="text-signal-600">↑{bytes(r.bytes_out)}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
