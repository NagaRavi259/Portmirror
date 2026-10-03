import { ArrowRight, ChevronDown, Plus, ShieldCheck, X } from "lucide-react";
import { FormEvent, KeyboardEvent, useMemo, useState } from "react";
import { Api, ApiError, Forward, ForwardBody, Protocol, SystemInfo } from "../lib/api";
import { ports, protoLabel } from "../lib/format";
import { SERVICE_PRESETS } from "../lib/presets";
import { useToast } from "./Toasts";
import { Drawer, Field, Segmented, Spinner, Switch, cx } from "./ui";

const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
const CIDR = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}(\/(3[0-2]|[12]?\d))?$/;

function ipToInt(ip: string) {
  return ip.split(".").reduce((a, o) => (a << 8) + Number(o), 0) >>> 0;
}
function inNet(ip: string, cidr: string) {
  const [net, bits] = cidr.split("/");
  const mask = bits === "0" ? 0 : (~0 << (32 - Number(bits))) >>> 0;
  return (ipToInt(ip) & mask) === (ipToInt(net) & mask);
}

type Expiry = "never" | "1h" | "8h" | "24h" | "7d" | "custom";

interface State {
  name: string; protocol: Protocol; listen: string; listenEnd: string; useRange: boolean;
  targetIp: string; targetPort: string; sources: string[]; rateLimit: string; maxConns: string; bandwidthLimit: string;
  schedOn: boolean; schedDays: number[]; schedStart: string; schedEnd: string;
  quotaGb: string; quotaPeriod: "day" | "week" | "month";
  expiry: Expiry; expiresCustom: string; enabled: boolean; description: string;
}

function initial(f?: Forward, lanNet = "192.168.88.0/24"): State {
  const toLocal = (iso: string) => {
    const d = new Date(iso);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  };
  return {
    name: f?.name ?? "", protocol: f?.protocol ?? "tcp", listen: f ? String(f.listen_port) : "",
    listenEnd: f?.listen_port_end ? String(f.listen_port_end) : "", useRange: !!f?.listen_port_end,
    targetIp: f?.target_ip ?? "", targetPort: f ? String(f.target_port) : "",
    sources: f?.allowed_sources ?? [lanNet], rateLimit: f?.rate_limit ? String(f.rate_limit) : "",
    maxConns: f?.max_conns ? String(f.max_conns) : "",
    bandwidthLimit: f?.bandwidth_limit_kbps ? String(f.bandwidth_limit_kbps) : "",
    schedOn: !!f?.access_window, schedDays: f?.access_window?.days ?? [0, 1, 2, 3, 4],
    schedStart: f?.access_window?.start ?? "09:00", schedEnd: f?.access_window?.end ?? "22:00",
    quotaGb: f?.quota ? String(+(f.quota.bytes / 1024 ** 3).toFixed(3)) : "", quotaPeriod: f?.quota?.period ?? "month",
    expiry: f?.expires_at ? "custom" : "never",
    expiresCustom: f?.expires_at ? toLocal(f.expires_at) : "", enabled: f?.enabled ?? true, description: f?.description ?? "",
  };
}

export function ForwardForm({ forward, all, system, onClose, onSaved }: {
  forward?: Forward; all: Forward[]; system: SystemInfo | null; onClose: () => void; onSaved: (f: Forward) => void;
}) {
  const toast = useToast();
  const [s, setS] = useState<State>(() => initial(forward, system?.lan_net));
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [serverErr, setServerErr] = useState<{ msg: string; fields: Record<string, string> } | null>(null);
  const [saving, setSaving] = useState(false);
  const [srcDraft, setSrcDraft] = useState("");
  const [showAdv, setShowAdv] = useState(!!(forward?.rate_limit || forward?.max_conns || forward?.bandwidth_limit_kbps || forward?.access_window || forward?.quota || forward?.expires_at));
  const [killOld, setKillOld] = useState(false);
  const set = <K extends keyof State>(k: K, v: State[K]) => setS((x) => ({ ...x, [k]: v }));

  const vpnNet = system?.vpn_net ?? "10.0.0.0/24";
  const maxRange = system?.max_range ?? 1024;
  const reserved = system?.reserved_tcp_ports ?? [8088];

  const lp = Number(s.listen), le = s.useRange && s.listenEnd ? Number(s.listenEnd) : null;
  const tp = Number(s.targetPort);
  const span = le ? le - lp + 1 : 1;

  const errors = useMemo(() => {
    const e: Record<string, string> = {};
    if (!s.name.trim()) e.name = "Give the forward a name";
    const okPort = (v: number) => Number.isInteger(v) && v >= 1 && v <= 65535;
    if (!okPort(lp)) e.listen = "1 – 65535";
    if (s.useRange) {
      if (!okPort(Number(s.listenEnd))) e.listenEnd = "1 – 65535";
      else if (le !== null && le < lp) e.listenEnd = "Must be ≥ start port";
      else if (span > maxRange) e.listenEnd = `Max ${maxRange} ports per range`;
    }
    if (!IPV4.test(s.targetIp)) e.targetIp = "Enter an IPv4 address";
    else if (!inNet(s.targetIp, vpnNet)) e.targetIp = `Must be inside the VPN network ${vpnNet}`;
    else if (s.targetIp === system?.gw_vpn_ip) e.targetIp = "That's the gateway itself";
    if (!okPort(tp)) e.targetPort = "1 – 65535";
    else if (tp + span - 1 > 65535) e.targetPort = "Target range runs past 65535";
    if (s.protocol !== "udp" && !e.listen && reserved.some((r) => r >= lp && r <= (le ?? lp)))
      e.listen = `TCP ${reserved[0]} is reserved for this UI`;
    if (!s.sources.length) e.sources = "Add at least one allowed source";
    if (s.rateLimit && !(Number(s.rateLimit) >= 1)) e.rateLimit = "≥ 1";
    if (s.maxConns && !(Number(s.maxConns) >= 1)) e.maxConns = "≥ 1";
    if (s.bandwidthLimit && !(Number(s.bandwidthLimit) >= 1)) e.bandwidthLimit = "≥ 1";
    if (s.expiry === "custom" && !s.expiresCustom) e.expiry = "Pick a date and time";
    if (s.schedOn && !s.schedDays.length) e.schedule = "Pick at least one day";
    if (s.quotaGb && !(Number(s.quotaGb) > 0)) e.quota = "Enter a positive amount, in GiB";
    if (s.schedOn && s.schedStart === s.schedEnd) e.schedule = "Start and end can't be the same time";
    if (!e.listen && !e.listenEnd && s.enabled) {
      const protos = s.protocol === "both" ? ["tcp", "udp"] : [s.protocol];
      const hit = all.find((o) => o.id !== forward?.id && o.enabled && !o.expired &&
        (o.protocol === "both" ? ["tcp", "udp"] : [o.protocol]).some((p) => protos.includes(p)) &&
        lp <= (o.listen_port_end ?? o.listen_port) && o.listen_port <= (le ?? lp));
      if (hit) e.listen = `Conflicts with “${hit.name}” (${protoLabel(hit.protocol)} ${ports(hit.listen_port, hit.listen_port_end)})`;
    }
    return e;
  }, [s, lp, le, tp, span, all, forward, vpnNet, maxRange, reserved, system]);

  const fieldErr = (k: string, serverKey?: string) =>
    (touched[k] || touched.__submit ? errors[k] : undefined) ?? (serverKey ? serverErr?.fields[serverKey] : undefined);

  const addSource = () => {
    const v = srcDraft.trim().replace(/,$/, "");
    if (!v) return;
    if (!CIDR.test(v)) { toast("error", `“${v}” isn't an IPv4 address or CIDR`); return; }
    if (!s.sources.includes(v)) set("sources", [...s.sources, v]);
    setSrcDraft("");
  };
  const onSrcKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addSource(); }
    if (e.key === "Backspace" && !srcDraft && s.sources.length) set("sources", s.sources.slice(0, -1));
  };

  const expiresAt = (): string | null => {
    const add = { "1h": 3600, "8h": 8 * 3600, "24h": 86400, "7d": 7 * 86400 } as Record<string, number>;
    if (s.expiry === "never") return null;
    if (s.expiry === "custom") return new Date(s.expiresCustom).toISOString();
    return new Date(Date.now() + add[s.expiry] * 1000).toISOString();
  };

  const targetChanged = !!forward && (forward.target_ip !== s.targetIp || forward.target_port !== tp);
  const live = forward?.stats?.live ?? 0;

  const submit = async (ev: FormEvent) => {
    ev.preventDefault();
    setTouched((t) => ({ ...t, __submit: true }));
    if (Object.keys(errors).length) return;
    const body: ForwardBody = {
      name: s.name.trim(), protocol: s.protocol, listen_port: lp, listen_port_end: le, target_ip: s.targetIp,
      target_port: tp, allowed_sources: s.sources, rate_limit: s.rateLimit ? Number(s.rateLimit) : null,
      max_conns: s.maxConns ? Number(s.maxConns) : null,
      bandwidth_limit_kbps: s.bandwidthLimit ? Number(s.bandwidthLimit) : null,
      access_window: s.schedOn ? { days: s.schedDays, start: s.schedStart, end: s.schedEnd } : null,
      quota: s.quotaGb ? { bytes: Math.round(Number(s.quotaGb) * 1024 ** 3), period: s.quotaPeriod } : null,
      expires_at: expiresAt(), enabled: s.enabled,
      description: s.description.trim(),
    };
    setSaving(true);
    setServerErr(null);
    try {
      const f = forward ? await Api.update(forward.id, body, killOld && targetChanged) : await Api.create(body);
      toast("ok", forward ? `Saved “${f.name}” — applied to the gateway` : `“${f.name}” is live on :${ports(f.listen_port, f.listen_port_end)}`);
      onSaved(f);
    } catch (e) {
      const err = e as ApiError;
      setServerErr({ msg: err.message, fields: err.fields ?? {} });
    } finally {
      setSaving(false);
    }
  };

  const gw = system?.gw_lan_ip ?? "192.168.88.8";

  return (
    <Drawer
      title={forward ? "Edit forward" : "New forward"}
      subtitle={forward ? `#${forward.id} · changes apply instantly, without dropping other traffic` : "Expose a remote service on the gateway's LAN address"}
      onClose={onClose}
      footer={<>
        <button type="button" className="btn-outline" onClick={onClose}>Cancel</button>
        <button type="submit" form="fwd-form" className="btn-primary min-w-[128px]" disabled={saving}>
          {saving ? <Spinner /> : forward ? "Save changes" : <><Plus size={16} /> Create forward</>}
        </button>
      </>}
    >
      <form id="fwd-form" onSubmit={submit} className="space-y-5" noValidate>
        {/* live mapping preview */}
        <div className="relative overflow-hidden rounded-xl border border-line bg-gradient-to-br from-accent-50/70 via-white to-signal-50/70 p-4">
          <div className="label mb-2">Mapping</div>
          <div className="flex items-center gap-2 font-mono text-[13px]">
            <div className="rounded-lg bg-white px-2.5 py-1.5 shadow-sm ring-1 ring-line">
              <span className="text-ink-400">{gw}:</span><span className="font-semibold text-ink-900">{s.listen || "—"}{le ? `–${le}` : ""}</span>
            </div>
            <div className="flex flex-1 flex-col items-center">
              <span className="text-[10px] font-semibold tracking-wider text-accent-500">{protoLabel(s.protocol)}</span>
              <div className="flex w-full items-center">
                <div className="h-px flex-1 bg-gradient-to-r from-accent-400 to-signal-400" />
                <ArrowRight size={14} className="-ml-1 text-signal-500" />
              </div>
            </div>
            <div className="rounded-lg bg-white px-2.5 py-1.5 shadow-sm ring-1 ring-line">
              <span className="text-ink-400">{s.targetIp || "—"}:</span><span className="font-semibold text-ink-900">{s.targetPort || "—"}{le && tp ? `–${tp + span - 1}` : ""}</span>
            </div>
          </div>
        </div>

        {serverErr && (
          <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2.5 text-sm text-rose-700">{serverErr.msg}</div>
        )}

        <Field label="Name" error={fieldErr("name", "name")}>
          <input className={cx("input", fieldErr("name", "name") && "input-invalid")} value={s.name} maxLength={64}
            placeholder="e.g. Office RDP" autoFocus={!forward}
            onChange={(e) => set("name", e.target.value)} onBlur={() => setTouched((t) => ({ ...t, name: true }))} />
        </Field>

        {!forward && (
          <Field label="Start from a service" hint="Fills in the protocol and port only - you still set the target">
            <select className="input" aria-label="Service preset" value=""
              onChange={(e) => {
                const p = SERVICE_PRESETS.find((x) => x.id === e.target.value);
                if (!p) return;
                set("protocol", p.protocol);
                set("listen", String(p.port));
                set("useRange", false);
                set("listenEnd", "");
                setTouched((t) => ({ ...t, listen: true }));   // so a taken port shows its conflict straight away
              }}>
              <option value="">Choose a preset…</option>
              {SERVICE_PRESETS.map((p) => <option key={p.id} value={p.id}>{p.label} · :{p.port}</option>)}
            </select>
          </Field>
        )}

        <Field label="Protocol">
          <Segmented value={s.protocol} onChange={(v) => set("protocol", v)}
            options={[{ value: "tcp", label: "TCP" }, { value: "udp", label: "UDP" }, { value: "both", label: "TCP + UDP" }]} />
        </Field>

        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[13px] font-medium text-ink-700">Local port</span>
            <label className="flex items-center gap-2 text-xs text-ink-500">
              Port range <Switch checked={s.useRange} onChange={(v) => set("useRange", v)} label="Port range" />
            </label>
          </div>
          <div className="flex items-start gap-2">
            <div className="flex-1">
              <input className={cx("input num", fieldErr("listen", "listen_port") && "input-invalid")} inputMode="numeric"
                placeholder={s.useRange ? "start" : "e.g. 3389"} value={s.listen}
                onChange={(e) => set("listen", e.target.value.replace(/\D/g, ""))} onBlur={() => setTouched((t) => ({ ...t, listen: true }))} />
            </div>
            {s.useRange && <>
              <span className="pt-2.5 text-ink-300">–</span>
              <div className="flex-1">
                <input className={cx("input num", fieldErr("listenEnd", "listen_port_end") && "input-invalid")} inputMode="numeric"
                  placeholder="end" value={s.listenEnd}
                  onChange={(e) => set("listenEnd", e.target.value.replace(/\D/g, ""))} onBlur={() => setTouched((t) => ({ ...t, listenEnd: true }))} />
              </div>
            </>}
          </div>
          {(fieldErr("listen", "listen_port") || fieldErr("listenEnd", "listen_port_end")) ?
            <p className="mt-1 text-xs text-rose-600">{fieldErr("listen", "listen_port") || fieldErr("listenEnd", "listen_port_end")}</p>
            : <p className="mt-1 text-xs text-ink-400">LAN clients connect to <span className="font-mono">{gw}</span> on this port{s.useRange ? " range" : ""}.</p>}
        </div>

        <div className="grid grid-cols-[1fr_130px] gap-3">
          <Field label="Remote IP" error={fieldErr("targetIp", "target_ip")} hint={`Inside ${vpnNet}`}>
            <input className={cx("input num", fieldErr("targetIp", "target_ip") && "input-invalid")} placeholder="10.0.0.112"
              value={s.targetIp} onChange={(e) => set("targetIp", e.target.value.trim())} onBlur={() => setTouched((t) => ({ ...t, targetIp: true }))} />
          </Field>
          <Field label={s.useRange ? "Remote start port" : "Remote port"} error={fieldErr("targetPort", "target_port")}
            hint={s.useRange && tp && span > 1 ? `→ ${tp}–${tp + span - 1}` : undefined}>
            <input className={cx("input num", fieldErr("targetPort", "target_port") && "input-invalid")} inputMode="numeric"
              placeholder="3389" value={s.targetPort}
              onChange={(e) => set("targetPort", e.target.value.replace(/\D/g, ""))} onBlur={() => setTouched((t) => ({ ...t, targetPort: true }))} />
          </Field>
        </div>

        <Field label="Allowed sources" error={fieldErr("sources", "allowed_sources")}
          hint={<span className="inline-flex items-center gap-1"><ShieldCheck size={12} /> Only these LAN addresses may use the forward. Enter to add.</span>}>
          <div className={cx("input flex h-auto min-h-10 flex-wrap items-center gap-1.5 py-1.5", fieldErr("sources") && "input-invalid")}>
            {s.sources.map((src) => (
              <span key={src} className="chip bg-canvas font-mono text-ink-700 ring-1 ring-line">
                {src}
                <button type="button" aria-label={`Remove ${src}`} className="text-ink-400 hover:text-rose-500"
                  onClick={() => set("sources", s.sources.filter((x) => x !== src))}><X size={12} /></button>
              </span>
            ))}
            <input className="min-w-[120px] flex-1 border-0 bg-transparent font-mono text-sm outline-none placeholder:text-ink-300"
              placeholder={s.sources.length ? "" : "192.168.88.0/24"} value={srcDraft}
              onChange={(e) => setSrcDraft(e.target.value)} onKeyDown={onSrcKey} onBlur={addSource} />
          </div>
        </Field>

        <div className="rounded-xl border border-line">
          <button type="button" onClick={() => setShowAdv((v) => !v)}
            className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium text-ink-700">
            Limits &amp; schedule
            <ChevronDown size={16} className={cx("text-ink-400 transition-transform", showAdv && "rotate-180")} />
          </button>
          {showAdv && (
            <div className="space-y-4 border-t border-line px-4 py-4">
              <div className="grid grid-cols-2 gap-3">
                <Field label="Rate limit" hint="new connections / s" error={fieldErr("rateLimit", "rate_limit")}>
                  <input className="input num" inputMode="numeric" placeholder="unlimited" value={s.rateLimit}
                    onChange={(e) => set("rateLimit", e.target.value.replace(/\D/g, ""))} />
                </Field>
                <Field label="Max connections" hint="concurrent" error={fieldErr("maxConns", "max_conns")}>
                  <input className="input num" inputMode="numeric" placeholder="unlimited" value={s.maxConns}
                    onChange={(e) => set("maxConns", e.target.value.replace(/\D/g, ""))} />
                </Field>
              </div>
              <Field label="Bandwidth limit" hint="kbit/s, each direction" error={fieldErr("bandwidthLimit", "bandwidth_limit_kbps")}>
                <input className="input num" inputMode="numeric" placeholder="unlimited" value={s.bandwidthLimit}
                  onChange={(e) => set("bandwidthLimit", e.target.value.replace(/\D/g, ""))} />
              </Field>
              <Field label="Data quota" hint="Total in + out per period; reaching it turns the forward off" error={fieldErr("quota", "quota")}>
                <div className="flex items-center gap-2">
                  <input className={cx("input num h-9 w-32", fieldErr("quota", "quota") && "input-invalid")} inputMode="decimal"
                    placeholder="no limit" aria-label="Quota amount in GiB" value={s.quotaGb}
                    onChange={(e) => set("quotaGb", e.target.value.replace(/[^0-9.]/g, ""))}
                    onBlur={() => setTouched((t) => ({ ...t, quota: true }))} />
                  <span className="text-ink-400">GiB per</span>
                  <Segmented value={s.quotaPeriod} onChange={(v) => set("quotaPeriod", v)}
                    options={[{ value: "day", label: "day" }, { value: "week", label: "week" }, { value: "month", label: "month" }]} />
                </div>
              </Field>
              <Field label="Only on a schedule" hint="Off outside these hours; a manual change holds until the next boundary"
                error={fieldErr("schedule", "access_window")}>
                <div className="space-y-3">
                  <Switch checked={s.schedOn} onChange={(v) => set("schedOn", v)} label="Only on a schedule" />
                  {s.schedOn && (<>
                    <div className="flex flex-wrap gap-1.5">
                      {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d, i) => {
                        const on = s.schedDays.includes(i);
                        return (
                          <button type="button" key={d} aria-pressed={on} onClick={() => set("schedDays", on ? s.schedDays.filter((x) => x !== i) : [...s.schedDays, i].sort())}
                            className={cx("h-8 min-w-[3rem] rounded-md px-2 text-xs font-semibold transition",
                              on ? "bg-accent-500 text-oncolor" : "bg-canvas text-ink-500 ring-1 ring-line hover:text-ink-900")}>{d}</button>
                        );
                      })}
                    </div>
                    <div className="flex items-center gap-2">
                      <input type="time" className="input num h-9 w-auto" value={s.schedStart} aria-label="Window start"
                        onChange={(e) => set("schedStart", e.target.value)} />
                      <span className="text-ink-400">to</span>
                      <input type="time" className="input num h-9 w-auto" value={s.schedEnd} aria-label="Window end"
                        onChange={(e) => set("schedEnd", e.target.value)} />
                    </div>
                  </>)}
                </div>
              </Field>
              <Field label="Auto-disable" error={fieldErr("expiry", "expires_at")}
                hint={s.expiry === "never" ? "The forward stays on until you turn it off." : "It switches itself off at that time; existing connections drain."}>
                <div className="flex flex-wrap gap-2">
                  <Segmented value={s.expiry} onChange={(v) => set("expiry", v)} options={[
                    { value: "never", label: "Never" }, { value: "1h", label: "1h" }, { value: "8h", label: "8h" },
                    { value: "24h", label: "24h" }, { value: "7d", label: "7d" }, { value: "custom", label: "Custom" },
                  ]} />
                  {s.expiry === "custom" && (
                    <input type="datetime-local" className="input h-9 w-auto" value={s.expiresCustom}
                      onChange={(e) => set("expiresCustom", e.target.value)} />
                  )}
                </div>
              </Field>
            </div>
          )}
        </div>

        <Field label="Description" hint="Optional note for your team">
          <textarea className="input h-20 resize-none py-2" maxLength={500} value={s.description}
            onChange={(e) => set("description", e.target.value)} />
        </Field>

        <label className="flex items-center justify-between rounded-xl border border-line px-4 py-3">
          <span>
            <span className="block text-sm font-medium text-ink-900">Enabled</span>
            <span className="text-xs text-ink-500">Disabled forwards keep their settings and traffic totals.</span>
          </span>
          <Switch checked={s.enabled} onChange={(v) => set("enabled", v)} label="Enabled" />
        </label>

        {targetChanged && live > 0 && (
          <label className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm">
            <input type="checkbox" className="mt-0.5 accent-amber-600" checked={killOld} onChange={(e) => setKillOld(e.target.checked)} />
            <span className="text-amber-900">
              Also cut the <b>{live}</b> live connection{live === 1 ? "" : "s"} still going to the old target.
              <span className="block text-amber-800/80">Otherwise they continue until they close; new connections use the new target.</span>
            </span>
          </label>
        )}
      </form>
    </Drawer>
  );
}
