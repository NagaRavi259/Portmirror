import { ArrowDownRight, ArrowRight, ArrowUpRight, Cable, Cpu, Gauge, Info, Network, Pencil, Plus, Search, Timer, Trash2, Zap } from "lucide-react";
import { ReactNode, useEffect, useMemo, useState } from "react";
import { Sparkline, TimeChart } from "../components/Charts";
import { Card, Empty, PortLink, ProtoBadge, Segmented, StatusDot, Switch, cx } from "../components/ui";
import { Api, Forward, History, Snapshot, SystemInfo } from "../lib/api";
import { bps, bytes, count, ports, rate, until } from "../lib/format";
import { go } from "../lib/router";
import { LIVE_WINDOW_S, RANGES, Range } from "../lib/ranges";

/** Plain-English explanations shown when hovering (or keyboard-focusing) a KPI card. */
const KPI_HELP: Record<string, { what: string }> = {
  Live: {
    what: "How many connections are open through the gateway right now, across all forwards. The small number underneath is how many new ones start each second.",
  },
  In: {
    what: "Data flowing from your local network out to the remote services, right now. The small line is the last minute.",
  },
  Out: {
    what: "Data coming back from the remote services to your local network, right now. The small line is the last minute.",
  },
  Forwards: {
    what: "Forwards switched on, out of all forwards you have. A forward connects a port on the gateway to a service on the remote network.",
  },
  Conntrack: {
    what: "The gateway keeps a list of every connection passing through it (the connection tracking table). This shows how many entries it holds and how full the list is.",
  },
  Gateway: {
    what: "How much processor (CPU) and memory the gateway itself is using.",
  },
};

/** Anchor the help panel to the side of the card that has room: right-column cards open leftwards
 *  (2 columns on phones, 3 on md, 6 on xl) so the panel never pushes past the screen edge. */
function helpAlign(i: number) {
  const side = (right: boolean, bp = "") => (right ? `${bp}right-0 ${bp}left-auto` : `${bp}left-0 ${bp}right-auto`);
  return [side(i % 2 === 1), side(i % 3 === 2, "md:"), side(i === 5, "xl:")].join(" ");
}

function Kpi({ icon, label, value, sub, accent, children, index = 0 }: {
  icon: ReactNode; label: string; value: ReactNode; sub?: ReactNode; accent?: string; children?: ReactNode; index?: number;
}) {
  const help = KPI_HELP[label];
  const helpId = `kpi-help-${label.toLowerCase()}`;
  return (
    <div className="group relative outline-none" tabIndex={help ? 0 : undefined} aria-describedby={help ? helpId : undefined}>
    <Card className="relative overflow-hidden p-4 transition group-hover:border-accent-100 group-hover:shadow-lift group-focus-visible:border-accent-400">
      <div className="flex items-center gap-2">
        <span className={cx("grid h-7 w-7 place-items-center rounded-lg", accent ?? "bg-accent-50 text-accent-500")}>{icon}</span>
        <span className="label truncate">{label}</span>
        {help && <Info size={13} className="ml-auto shrink-0 text-ink-300 transition group-hover:text-accent-500" aria-hidden />}
      </div>
      <div className="relative mt-3">
        {children && <div className="pointer-events-none absolute -right-1 bottom-4 opacity-70">{children}</div>}
        <div className="num relative whitespace-nowrap text-[22px] font-medium leading-none text-ink-950 2xl:text-[26px]">{value}</div>
        {sub && <div className="relative mt-1.5 truncate text-xs text-ink-500">{sub}</div>}
      </div>
    </Card>
      {help && (
        <div id={helpId} role="tooltip"
          className={cx("pointer-events-none invisible absolute top-full z-30 mt-2 w-[min(18rem,calc(100vw-2.5rem))] translate-y-1", helpAlign(index),
            " rounded-xl border border-line bg-white/95 p-3.5 text-left opacity-0 shadow-lift backdrop-blur transition duration-150 group-hover:visible group-hover:translate-y-0 group-hover:opacity-100 group-focus-visible:visible group-focus-visible:translate-y-0 group-focus-visible:opacity-100")}>
          <p className="text-[12.5px] leading-5 text-ink-900">{help.what}</p>
        </div>
      )}
    </div>
  );
}

export function forwardState(f: Forward): "up" | "down" | "off" | "unknown" {
  if (!f.enabled || f.expired) return "off";
  return f.health?.state ?? "unknown";
}

export function Dashboard({ forwards, snap, loading, system, onNew, onEdit, onRemove, onToggle }: {
  forwards: Forward[]; snap: Snapshot | null; loading: boolean; system: SystemInfo | null;
  onNew: () => void; onEdit: (f: Forward) => void; onRemove: (f: Forward) => void; onToggle: (f: Forward, on: boolean) => void;
}) {
  const [q, setQ] = useState("");
  const [range, setRange] = useState<Range>("live");
  const [hist, setHist] = useState<History | null>(null);
  const g = snap?.global;

  useEffect(() => {
    let alive = true;
    Api.globalHistory(range).then((h) => alive && setHist(h)).catch(() => {});
    const t = range === "live" || range === "1h" ? undefined : window.setInterval(() => Api.globalHistory(range).then((h) => alive && setHist(h)), 60_000);
    return () => { alive = false; window.clearInterval(t); };
  }, [range]);

  // extend the live (every snapshot, ~1 s) and 1h (every 5 s) charts from the stream
  useEffect(() => {
    if ((range !== "live" && range !== "1h") || !snap || !hist) return;
    const t = Math.floor(snap.ts);
    const last = hist.points[hist.points.length - 1];
    if (last && t - last.t < (range === "live" ? 1 : 5)) return;
    const keep = range === "live" ? hist.points.filter((p) => p.t > t - LIVE_WINDOW_S - 5) : hist.points.slice(-719);
    setHist({ ...hist, points: [...keep, { t, new_per_s: snap.global.new_per_s,
      in_bps: snap.global.in_bps, out_bps: snap.global.out_bps, live: snap.global.live }] });
  }, [snap, range, hist]);

  const chart = useMemo(() => {
    const p = hist?.points ?? [];
    return [p.map((x) => x.t), p.map((x) => x.in_bps), p.map((x) => x.out_bps)];
  }, [hist]);

  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return forwards;
    return forwards.filter((f) => [f.name, f.description, f.target_ip, String(f.listen_port), String(f.target_port), f.protocol]
      .some((x) => x.toLowerCase().includes(s)));
  }, [q, forwards]);

  const down = forwards.filter((f) => forwardState(f) === "down").length;
  const ctPct = g ? (g.conntrack_count / Math.max(1, g.conntrack_max)) * 100 : 0;
  const globalSpark = (i: 1 | 2) => chart[i].slice(-60);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink-950">Port forwards</h1>
          <p className="mt-1 text-sm text-ink-500">Local ports on the gateway, mirrored to services across the VPN — changes apply live.</p>
        </div>
        <button className="btn-primary h-10 px-4" onClick={onNew}><Plus size={17} /> New forward</button>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
        <Kpi index={0} icon={<Cable size={15} />} label="Live" value={count(g?.live)}
          sub={<><span className="num text-ink-700">{rate(g?.new_per_s)}</span> new / s</>} />
        <Kpi index={1} icon={<ArrowDownRight size={15} />} label="In" value={bps(g?.in_bps)} sub="LAN → remote"
          accent="bg-accent-50 text-accent-500"><Sparkline values={globalSpark(1)} width={72} height={30} /></Kpi>
        <Kpi index={2} icon={<ArrowUpRight size={15} />} label="Out" value={bps(g?.out_bps)} sub="remote → LAN"
          accent="bg-signal-50 text-signal-600"><Sparkline values={globalSpark(2)} width={72} height={30} color="#06b6d4" /></Kpi>
        <Kpi index={3} icon={<Network size={15} />} label="Forwards" value={<>{g?.forwards_active ?? "–"}<span className="text-base text-ink-300">/{g?.forwards_total ?? "–"}</span></>}
          sub={down ? <span className="text-rose-600">{down} target{down > 1 ? "s" : ""} unreachable</span> : "all targets healthy"}
          accent={down ? "bg-rose-50 text-rose-500" : "bg-emerald-50 text-emerald-600"} />
        <Kpi index={4} icon={<Gauge size={15} />} label="Conntrack" value={count(g?.conntrack_count)}
          sub={<span className="flex items-center gap-2"><span className="h-1.5 w-16 overflow-hidden rounded-full bg-ink-950/[.06]">
            <span className={cx("block h-full rounded-full", ctPct > 80 ? "bg-rose-500" : "bg-gradient-to-r from-accent-500 to-signal-500")}
              style={{ width: `${Math.max(2, Math.min(100, ctPct))}%` }} /></span>{ctPct.toFixed(1)}% of {count(g?.conntrack_max)}</span>} />
        <Kpi index={5} icon={<Cpu size={15} />} label="Gateway" value={`${g?.system.cpu_pct.toFixed(1) ?? "–"}%`}
          sub={`CPU · ${bytes(g?.system.mem_bytes)} RAM`} accent="bg-ink-950/[.05] text-ink-700" />
      </div>

      <Card className="p-5">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-ink-900">Throughput through the gateway</h2>
            <div className="mt-1 flex items-center gap-4 text-xs text-ink-500">
              <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-accent-500" />In (LAN → remote)</span>
              <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-signal-500" />Out (remote → LAN)</span>
            </div>
          </div>
          <Segmented value={range} onChange={setRange} options={RANGES} />
        </div>
        {chart[0].length > 1 ? (
          <TimeChart data={chart} height={200} yFmt={bps} windowS={range === "live" ? LIVE_WINDOW_S : undefined}
            series={[{ label: "In", color: "#4f46e5", fill: true, fmt: bps }, { label: "Out", color: "#06b6d4", fill: true, fmt: bps }]} />
        ) : (
          <div className="grid h-[200px] place-items-center text-sm text-ink-400">
            {range === "live" || range === "1h" ? "Collecting samples…" : "History builds up minute by minute — check back soon."}
          </div>
        )}
      </Card>

      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3.5">
          <h2 className="text-sm font-semibold text-ink-900">All forwards</h2>
          <span className="chip bg-canvas text-ink-500 ring-1 ring-line">{forwards.length}</span>
          <div className="relative ml-auto w-full sm:w-72">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" />
            <input className="input h-9 pl-9" placeholder="Search name, port, IP…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search forwards" />
          </div>
        </div>

        {!loading && forwards.length === 0 ? (
          <Empty icon={<Zap size={22} />} title="No forwards yet">
            Create one to expose a remote service on the gateway's LAN address.
            <div className="mt-4"><button className="btn-primary" onClick={onNew}><Plus size={16} /> New forward</button></div>
          </Empty>
        ) : (
          <>
          <ul className="divide-y divide-line md:hidden">
            {shown.map((f) => {
              const st = f.stats;
              const state = forwardState(f);
              return (
                <li key={f.id} className={cx("flex items-start gap-3 px-4 py-3.5", state === "off" && "opacity-60")}
                  onClick={() => go(`forwards/${f.id}`)}>
                  <span className="mt-1.5"><StatusDot state={state} pulse={state === "up" && (st?.live ?? 0) > 0} /></span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium text-ink-900">{f.name}</div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 font-mono text-[12px]">
                      <ProtoBadge protocol={f.protocol} />
                      <PortLink forward={f} gwLanIp={system?.gw_lan_ip} className="font-semibold text-ink-900 hover:text-accent-600 hover:underline" />
                      <ArrowRight size={12} className="text-ink-300" />
                      <span className="text-ink-700">{f.target_ip}:{ports(f.target_port, f.target_port_end)}</span>
                    </div>
                    <div className="num mt-1.5 flex gap-3 text-[11.5px] text-ink-500">
                      <span>{count(st?.live)} live</span>
                      <span className="text-accent-600">↓ {bps(st?.in_bps)}</span>
                      <span className="text-signal-600">↑ {bps(st?.out_bps)}</span>
                    </div>
                  </div>
                  <div onClick={(e) => e.stopPropagation()} className="pt-1">
                    <Switch checked={f.enabled && !f.expired} onChange={(v) => onToggle(f, v)} label={`Enable ${f.name}`} />
                  </div>
                </li>
              );
            })}
          </ul>
          <div className="hidden overflow-x-auto scroll-thin md:block">
            <table className="w-full min-w-[980px] text-sm">
              <thead>
                <tr className="border-b border-line text-left">
                  {["Forward", "Mapping", "Traffic", "Live", "New / s", "Totals", "", ""].map((h, i) => (
                    <th key={i} className={cx("label px-5 py-2.5 font-semibold", i >= 3 && i <= 5 && "text-right")}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shown.map((f) => {
                  const st = f.stats;
                  const state = forwardState(f);
                  const spark = (f.spark ?? []).map((p) => p[1]);
                  return (
                    <tr key={f.id} onClick={() => go(`forwards/${f.id}`)}
                      className={cx("group cursor-pointer border-b border-line/70 transition last:border-0 hover:bg-accent-50/30",
                        state === "off" && "opacity-60")}>
                      <td className="px-5 py-3.5">
                        <div className="flex items-center gap-3">
                          <StatusDot state={state} pulse={state === "up" && (st?.live ?? 0) > 0} />
                          <div className="min-w-0">
                            <div className="truncate font-medium text-ink-900">{f.name}</div>
                            <div className="flex items-center gap-2 text-xs text-ink-400">
                              {state === "down" ? <span className="text-rose-600">target unreachable</span>
                                : state === "off" ? (f.expired ? "expired" : "disabled")
                                  : f.health?.latency_ms != null ? <span className="num">{f.health.latency_ms} ms</span> : "checking…"}
                              {f.expires_at && !f.expired && <span className="flex items-center gap-1 text-amber-600"><Timer size={11} />{until(f.expires_at)}</span>}
                              {(f.rate_limit || f.max_conns) && <span className="chip bg-amber-50 text-amber-700">limited</span>}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="px-5 py-3.5">
                        <div className="flex items-center gap-2 font-mono text-[12.5px]">
                          <ProtoBadge protocol={f.protocol} />
                          <PortLink forward={f} gwLanIp={system?.gw_lan_ip} className="font-semibold text-ink-900 hover:text-accent-600 hover:underline" />
                          <ArrowRight size={13} className="text-ink-300" />
                          <span className="text-ink-700">{f.target_ip}:{ports(f.target_port, f.target_port_end)}</span>
                        </div>
                      </td>
                      <td className="px-5 py-3.5">
                        <div className="flex items-center gap-3">
                          <Sparkline values={spark} width={88} height={26} />
                          <div className="num text-[11.5px] leading-4">
                            <div className="text-accent-600">↓ {bps(st?.in_bps)}</div>
                            <div className="text-signal-600">↑ {bps(st?.out_bps)}</div>
                          </div>
                        </div>
                      </td>
                      <td className="num px-5 py-3.5 text-right text-ink-900">{count(st?.live)}</td>
                      <td className="num px-5 py-3.5 text-right text-ink-700">{rate(st?.new_per_s)}</td>
                      <td className="px-5 py-3.5 text-right">
                        <div className="num text-ink-700">{count(st?.new_total)} conns</div>
                        <div className="num text-xs text-ink-400">{bytes((st?.bytes_in_total ?? 0) + (st?.bytes_out_total ?? 0))}</div>
                      </td>
                      <td className="px-3 py-3.5" onClick={(e) => e.stopPropagation()}>
                        <Switch checked={f.enabled && !f.expired} onChange={(v) => onToggle(f, v)} label={`Enable ${f.name}`} />
                      </td>
                      <td className="px-3 py-3.5" onClick={(e) => e.stopPropagation()}>
                        <div className="flex justify-end gap-0.5 opacity-60 transition group-hover:opacity-100">
                          <button className="btn-ghost h-8 w-8 px-0" onClick={() => onEdit(f)} aria-label={`Edit ${f.name}`} title="Edit"><Pencil size={15} /></button>
                          <button className="btn-ghost h-8 w-8 px-0 hover:text-rose-600" onClick={() => onRemove(f)} aria-label={`Delete ${f.name}`} title="Delete"><Trash2 size={15} /></button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
                {shown.length === 0 && forwards.length > 0 && (
                  <tr><td colSpan={8} className="px-5 py-10 text-center text-sm text-ink-400">No forwards match “{q}”.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          </>
        )}
      </Card>
    </div>
  );
}
