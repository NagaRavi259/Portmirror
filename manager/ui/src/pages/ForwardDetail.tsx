import { ArrowLeft, ArrowRight, Ban, Clock, Pencil, ShieldCheck, Timer, Trash2, Unplug, Users } from "lucide-react";
import { ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { TimeChart } from "../components/Charts";
import { useToast } from "../components/Toasts";
import { Card, Empty, Modal, ProtoBadge, Segmented, Spinner, StatusDot, Switch, cx } from "../components/ui";
import { Api, AuditEntry, Conn, Forward, History, Snapshot, SystemInfo } from "../lib/api";
import { ago, bps, bytes, count, datetime, duration, ports, rate, until } from "../lib/format";
import { forwardState } from "./Dashboard";
import { LIVE_WINDOW_S, RANGES, Range } from "../lib/ranges";

function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return (
    <div className="px-5 py-4">
      <div className="label">{label}</div>
      <div className={cx("num mt-1.5 whitespace-nowrap text-lg font-medium 2xl:text-xl", tone ?? "text-ink-950")}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-ink-400">{sub}</div>}
    </div>
  );
}

const STATE_TONE: Record<string, string> = {
  ESTABLISHED: "bg-emerald-50 text-emerald-700", ASSURED: "bg-emerald-50 text-emerald-700", ACTIVE: "bg-signal-50 text-signal-600",
  SYN_SENT: "bg-amber-50 text-amber-700", SYN_RECV: "bg-amber-50 text-amber-700", UNREPLIED: "bg-amber-50 text-amber-700",
  TIME_WAIT: "bg-ink-950/[.05] text-ink-500", CLOSE: "bg-ink-950/[.05] text-ink-500", CLOSE_WAIT: "bg-ink-950/[.05] text-ink-500",
  FIN_WAIT: "bg-ink-950/[.05] text-ink-500", LAST_ACK: "bg-ink-950/[.05] text-ink-500",
};

export function ForwardDetail({ forward, snap, system, onEdit, onRemove, onToggle }: {
  forward: Forward | undefined; snap: Snapshot | null; system: SystemInfo | null;
  onEdit: (f: Forward) => void; onRemove: (f: Forward) => void; onToggle: (f: Forward, on: boolean) => void;
}) {
  const toast = useToast();
  const [range, setRange] = useState<Range>("live");
  const [hist, setHist] = useState<History | null>(null);
  const [conns, setConns] = useState<{ total: number; connections: Conn[] } | null>(null);
  const [showClosed, setShowClosed] = useState(false);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [confirmKillAll, setConfirmKillAll] = useState(false);
  const [killing, setKilling] = useState<string | null>(null);
  const id = forward?.id;

  useEffect(() => {
    if (!id) return;
    let alive = true;
    const load = () => Api.history(id, range).then((h) => alive && setHist(h)).catch(() => {});
    load();
    const t = range === "live" ? undefined : window.setInterval(load, range === "1h" ? 5000 : 60_000);
    return () => { alive = false; window.clearInterval(t); };
  }, [id, range]);

  const loadConns = useCallback(() => {
    if (id) Api.connections(id).then(setConns).catch(() => {});
  }, [id]);
  useEffect(() => {
    loadConns();
    const t = window.setInterval(loadConns, 2000);
    return () => window.clearInterval(t);
  }, [loadConns]);

  useEffect(() => {
    if (!id) return;
    Api.audit(300).then((a) => setAudit(a.filter((x) => x.target?.startsWith(`#${id} `)).slice(0, 8))).catch(() => {});
  }, [id, forward?.updated_at]);

  const liveSt = id ? snap?.forwards[String(id)] : undefined;
  useEffect(() => {
    if ((range !== "live" && range !== "1h") || !snap || !liveSt) return;
    setHist((h) => {
      const t = Math.floor(snap.ts);
      const pts = h?.points ?? [];
      const last = pts[pts.length - 1];
      if (last && t - last.t < (range === "live" ? 1 : 5)) return h;
      const keep = range === "live" ? pts.filter((p) => p.t > t - LIVE_WINDOW_S - 5) : pts.slice(-719);
      return { range, step_s: h?.step_s ?? 1, points: [...keep, { t,
        new_per_s: liveSt.new_per_s, in_bps: liveSt.in_bps, out_bps: liveSt.out_bps, live: liveSt.live }] };
    });
  }, [snap, liveSt, range]);

  const tput = useMemo(() => {
    const p = hist?.points ?? [];
    return [p.map((x) => x.t), p.map((x) => x.in_bps), p.map((x) => x.out_bps)];
  }, [hist]);
  const connsChart = useMemo(() => {
    const p = hist?.points ?? [];
    return [p.map((x) => x.t), p.map((x) => x.live), p.map((x) => x.new_per_s)];
  }, [hist]);

  if (!forward) {
    return (
      <Card className="p-6">
        <a href="#/" className="btn-ghost -ml-2 mb-2"><ArrowLeft size={16} /> All forwards</a>
        <Empty icon={<Ban size={22} />} title="Forward not found">It may have been deleted.</Empty>
      </Card>
    );
  }

  const st = snap?.forwards[String(forward.id)] ?? forward.stats;
  const state = forwardState(forward);
  const list = (conns?.connections ?? []).filter((c) => showClosed || !["TIME_WAIT", "CLOSE"].includes(c.state));

  const killOne = async (c: Conn) => {
    const key = `${c.src}:${c.sport}`;
    setKilling(key);
    try {
      await Api.killOne(c);
      toast("ok", `Cut ${c.proto.toUpperCase()} ${key}`);
      loadConns();
    } catch (e) {
      toast("error", (e as Error).message);
    } finally {
      setKilling(null);
    }
  };

  const killAll = async () => {
    const r = await Api.killAll(forward.id);
    toast("ok", `Cut ${r.killed} connection${r.killed === 1 ? "" : "s"}`);
    setConfirmKillAll(false);
    loadConns();
  };

  return (
    <div className="space-y-6">
      <div>
        <a href="#/" className="btn-ghost -ml-3 h-8 text-ink-500"><ArrowLeft size={15} /> All forwards</a>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <StatusDot state={state} pulse={state === "up"} />
          <h1 className="text-2xl font-semibold tracking-tight text-ink-950">{forward.name}</h1>
          <ProtoBadge protocol={forward.protocol} />
          <span className="chip bg-canvas text-ink-400 ring-1 ring-line">#{forward.id}</span>
          <div className="ml-auto flex items-center gap-2">
            <label className="mr-2 flex items-center gap-2 text-sm text-ink-500">
              {forward.enabled && !forward.expired ? "Enabled" : "Disabled"}
              <Switch checked={forward.enabled && !forward.expired} onChange={(v) => onToggle(forward, v)} label="Enabled" />
            </label>
            <button className="btn-outline" onClick={() => onEdit(forward)}><Pencil size={15} /> Edit</button>
            <button className="btn-outline hover:border-rose-300 hover:text-rose-600" onClick={() => onRemove(forward)}><Trash2 size={15} /> Delete</button>
          </div>
        </div>
        {forward.description && <p className="mt-1.5 text-sm text-ink-500">{forward.description}</p>}
      </div>

      {/* mapping banner */}
      <Card className="relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-r from-accent-50/80 via-white/0 to-signal-50/80" />
        <div className="relative grid gap-6 p-5 md:grid-cols-[1fr_auto_1fr] md:items-center">
          <div>
            <div className="label">Local (LAN)</div>
            <div className="num mt-1 text-lg text-ink-950">{system?.gw_lan_ip ?? "gateway"}<span className="text-accent-500">:{ports(forward.listen_port, forward.listen_port_end)}</span></div>
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-ink-500">
              <ShieldCheck size={13} className="text-emerald-500" /> from
              {forward.allowed_sources.map((s) => <span key={s} className="chip bg-white font-mono text-ink-700 ring-1 ring-line">{s}</span>)}
            </div>
          </div>
          <div className="flex flex-col items-center px-4 text-center">
            <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-accent-500">DNAT + masquerade</span>
            <div className="my-1 flex w-40 items-center">
              <span className="h-px flex-1 bg-gradient-to-r from-accent-400 to-signal-400" />
              <ArrowRight size={16} className="-ml-1 text-signal-500" />
            </div>
            <span className="text-[11px] text-ink-400">via {system?.vpn_if ?? "vpn"} · as {system?.gw_vpn_ip ?? "gateway"}</span>
          </div>
          <div className="md:text-right">
            <div className="label">Remote (VPN)</div>
            <div className="num mt-1 text-lg text-ink-950">{forward.target_ip}<span className="text-signal-600">:{ports(forward.target_port, forward.target_port_end)}</span></div>
            <div className="mt-1 text-xs text-ink-500">
              {forward.health ? <>health <span className={forward.health.state === "up" ? "text-emerald-600" : "text-rose-600"}>{forward.health.state}</span>
                {forward.health.latency_ms != null && <> · <span className="num">{forward.health.latency_ms} ms</span></>} · {forward.health.method} · {ago(forward.health.checked_at)}</>
                : state === "off" ? "not probed while disabled" : "checking…"}
            </div>
          </div>
        </div>
        {(forward.rate_limit || forward.max_conns || forward.expires_at) && (
          <div className="relative flex flex-wrap gap-2 border-t border-line/80 px-5 py-2.5 text-xs">
            {forward.rate_limit && <span className="chip bg-amber-50 text-amber-700"><Clock size={12} /> ≤ {forward.rate_limit} new conns/s</span>}
            {forward.max_conns && <span className="chip bg-amber-50 text-amber-700"><Users size={12} /> ≤ {forward.max_conns} concurrent</span>}
            {forward.expires_at && <span className="chip bg-amber-50 text-amber-700"><Timer size={12} /> {forward.expired ? "expired" : `auto-disables ${until(forward.expires_at)}`}</span>}
          </div>
        )}
      </Card>

      <Card className="grid grid-cols-2 overflow-hidden sm:grid-cols-4 2xl:grid-cols-8 [&>*]:border-b [&>*]:border-r [&>*]:border-line [&>*]:-mb-px [&>*]:-mr-px">
        <Stat label="Live" value={count(st?.live)} sub={`${count(st?.flows)} tracked flows`} />
        <Stat label="New / s" value={rate(st?.new_per_s)} />
        <Stat label="In" value={bps(st?.in_bps)} tone="text-accent-600" sub="LAN → remote" />
        <Stat label="Out" value={bps(st?.out_bps)} tone="text-signal-600" sub="remote → LAN" />
        <Stat label="Connections" value={count(st?.new_total)} sub="since gateway start" />
        <Stat label="Bytes in" value={bytes(st?.bytes_in_total)} sub={`${count(st?.pkts_in_total)} pkts`} />
        <Stat label="Bytes out" value={bytes(st?.bytes_out_total)} sub={`${count(st?.pkts_out_total)} pkts`} />
        <Stat label="Blocked" value={count((st?.rate_limited_total ?? 0) + (st?.conn_limited_total ?? 0))}
          sub={forward.rate_limit || forward.max_conns ? "by limits" : "no limits set"}
          tone={(st?.rate_limited_total ?? 0) + (st?.conn_limited_total ?? 0) > 0 ? "text-amber-600" : undefined} />
      </Card>

      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink-900">History</h2>
        <Segmented value={range} onChange={setRange} options={RANGES} />
      </div>
      <div className="grid gap-6 xl:grid-cols-2">
        <Card className="p-5">
          <div className="mb-1 text-sm font-semibold text-ink-900">Throughput</div>
          {tput[0].length > 1 ? <TimeChart data={tput} height={190} yFmt={bps} windowS={range === "live" ? LIVE_WINDOW_S : undefined}
            series={[{ label: "In", color: "#4f46e5", fill: true, fmt: bps }, { label: "Out", color: "#06b6d4", fill: true, fmt: bps }]} />
            : <div className="grid h-[190px] place-items-center text-sm text-ink-400">No samples yet for this range</div>}
        </Card>
        <Card className="p-5">
          <div className="mb-1 text-sm font-semibold text-ink-900">Connections</div>
          {connsChart[0].length > 1 ? <TimeChart data={connsChart} height={190} yFmt={(v) => rate(v)} windowS={range === "live" ? LIVE_WINDOW_S : undefined}
            series={[{ label: "Live", color: "#4f46e5", fill: true, fmt: (v) => count(v) }, { label: "New / s", color: "#f59e0b", fmt: rate }]} />
            : <div className="grid h-[190px] place-items-center text-sm text-ink-400">No samples yet for this range</div>}
        </Card>
      </div>

      <div className="grid gap-6 xl:grid-cols-[1fr_320px]">
        <Card className="overflow-hidden">
          <div className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3.5">
            <h2 className="text-sm font-semibold text-ink-900">Live connections</h2>
            <span className="chip bg-canvas text-ink-500 ring-1 ring-line">{list.length}</span>
            <label className="ml-auto flex items-center gap-2 text-xs text-ink-500">
              Show closing <Switch checked={showClosed} onChange={setShowClosed} label="Show closing connections" />
            </label>
            <button className="btn-outline h-8 text-xs hover:border-rose-300 hover:text-rose-600" disabled={!list.length}
              onClick={() => setConfirmKillAll(true)}><Unplug size={14} /> Cut all</button>
          </div>
          {list.length === 0 ? (
            <Empty icon={<Users size={20} />} title="No live connections">Connections through this forward appear here within a couple of seconds.</Empty>
          ) : (
            <div className="max-h-[420px] overflow-auto scroll-thin">
              <table className="w-full min-w-[640px] text-sm">
                <thead className="sticky top-0 bg-white/95 backdrop-blur">
                  <tr className="border-b border-line text-left">
                    {["Client", "State", "Remote", "In", "Out", "Expires", ""].map((h, i) => (
                      <th key={i} className={cx("label px-5 py-2.5", (i === 3 || i === 4 || i === 5) && "text-right")}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="whitespace-nowrap font-mono text-[12.5px]">
                  {list.map((c) => {
                    const key = `${c.proto}-${c.src}-${c.sport}`;
                    return (
                      <tr key={key} className="border-b border-line/70 last:border-0 hover:bg-canvas/60">
                        <td className="px-5 py-2.5 text-ink-900">{c.src}<span className="text-ink-400">:{c.sport}</span></td>
                        <td className="px-5 py-2.5"><span className={cx("chip font-sans", STATE_TONE[c.state] ?? "bg-canvas text-ink-500")}>{c.proto.toUpperCase()} · {c.state}</span></td>
                        <td className="px-5 py-2.5 text-ink-500">{c.reply_src}:{c.reply_sport}</td>
                        <td className="px-5 py-2.5 text-right text-accent-600">{bytes(c.bytes_in)}</td>
                        <td className="px-5 py-2.5 text-right text-signal-600">{bytes(c.bytes_out)}</td>
                        <td className="px-5 py-2.5 text-right text-ink-400">{duration(c.ttl)}</td>
                        <td className="px-3 py-2.5 text-right">
                          <button className="btn-ghost h-7 px-2 text-xs text-ink-500 hover:text-rose-600" onClick={() => killOne(c)}
                            disabled={killing === `${c.src}:${c.sport}`} aria-label={`Cut connection from ${c.src}:${c.sport}`}>
                            {killing === `${c.src}:${c.sport}` ? <Spinner className="h-3 w-3" /> : <Unplug size={13} />} Cut
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <div className="space-y-6">
          <Card className="p-5">
            <h2 className="mb-3 text-sm font-semibold text-ink-900">Top clients</h2>
            {(st?.top_clients ?? []).length === 0 ? <p className="text-sm text-ink-400">No active clients.</p> : (
              <ul className="space-y-2.5">
                {st!.top_clients.map((c) => {
                  const max = Math.max(...st!.top_clients.map((x) => x.conns), 1);
                  return (
                    <li key={c.ip}>
                      <div className="flex items-center justify-between font-mono text-[12.5px]">
                        <span className="text-ink-900">{c.ip}</span>
                        <span className="text-ink-500">{c.conns} · {bytes(c.bytes)}</span>
                      </div>
                      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink-950/[.05]">
                        <div className="h-full rounded-full bg-gradient-to-r from-accent-500 to-signal-500" style={{ width: `${(c.conns / max) * 100}%` }} />
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>
          <Card className="p-5">
            <h2 className="mb-3 text-sm font-semibold text-ink-900">Recent activity</h2>
            {audit.length === 0 ? <p className="text-sm text-ink-400">No changes recorded.</p> : (
              <ol className="relative space-y-3 border-l border-line pl-4">
                {audit.map((a) => (
                  <li key={a.id} className="relative">
                    <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-white bg-accent-400" />
                    <div className="text-[13px] text-ink-900">{a.action.replace("forward.", "").replace("connections.", "")}</div>
                    <div className="text-xs text-ink-400">{a.actor} · {datetime(a.ts)}</div>
                  </li>
                ))}
              </ol>
            )}
          </Card>
        </div>
      </div>

      {confirmKillAll && (
        <Modal title={`Cut all connections of “${forward.name}”?`} onClose={() => setConfirmKillAll(false)}
          icon={<span className="grid h-9 w-9 place-items-center rounded-xl bg-rose-50 text-rose-500"><Unplug size={18} /></span>}
          footer={<>
            <button className="btn-outline" onClick={() => setConfirmKillAll(false)}>Cancel</button>
            <button className="btn-danger" onClick={killAll}>Cut {list.length}</button>
          </>}>
          TCP sessions get a reset and UDP flows stop. The forward stays enabled, so clients can reconnect.
        </Modal>
      )}
    </div>
  );
}
