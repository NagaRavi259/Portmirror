import { Check, Copy, Download, KeyRound, Lock, Palette, Server, Trash2, Upload } from "lucide-react";
import { ChangeEvent, FormEvent, ReactNode, useEffect, useState } from "react";
import { useToast } from "../components/Toasts";
import { Card, Field, Modal, Segmented, Spinner, Switch } from "../components/ui";
import { AccessStatus, Api, ForwardBody, SystemInfo, TlsStatus, TokenRow } from "../lib/api";
import { ago, bytes, datetime } from "../lib/format";
import { setTheme, storedTheme, THEME_CHANGE_EVENT } from "../lib/theme";

function Section({ icon, title, text, children }: { icon: ReactNode; title: string; text: string; children: ReactNode }) {
  return (
    <Card className="grid gap-6 p-6 lg:grid-cols-[280px_1fr]">
      <div>
        <div className="mb-2 grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-accent-50 to-signal-50 text-accent-500">{icon}</div>
        <h2 className="font-semibold text-ink-950">{title}</h2>
        <p className="mt-1 text-sm text-ink-500">{text}</p>
      </div>
      <div className="min-w-0">{children}</div>
    </Card>
  );
}

function HttpsSection() {
  const toast = useToast();
  const [st, setSt] = useState<TlsStatus | null>(null);
  const [cert, setCert] = useState<File | null>(null);
  const [key, setKey] = useState<File | null>(null);
  const [ca, setCa] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () => Api.tls().then(setSt).catch(() => {});
  useEffect(() => { load(); }, []);
  const upload = async () => {
    if (!cert || !key) { toast("error", "Choose a certificate and its private key"); return; }
    setBusy(true);
    try {
      const s = await Api.tlsCertificate(await cert.text(), await key.text(), ca ? await ca.text() : undefined);
      setSt(s); toast("ok", "Certificate stored - you can now switch to HTTPS");
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Upload failed");
    } finally { setBusy(false); }
  };
  const switchTo = async (on: boolean) => {
    try {
      const s = await Api.tlsSwitch(on); setSt(s);
      toast("ok", on
        ? `Restarting on HTTPS. Open https://${location.hostname}:${location.port} and press Keep within ${s.confirm_seconds} s`
        : `Restarting on HTTP. Confirm within ${s.confirm_seconds} s or it returns to HTTPS`);
    } catch (e) { toast("error", e instanceof Error ? e.message : "Could not switch"); }
  };
  return (
    <Section icon={<Lock size={18} />} title="HTTPS" text="Use your own certificate so the dashboard runs over HTTPS (Android needs this to install the app). Switching restarts the dashboard; you then have 60 seconds to confirm, or it goes back to the previous setting.">
      <div className="grid gap-4">
        <p className="text-sm text-ink-600">
          Certificate: <b>{st?.has_certificate ? "stored" : "none"}</b> · Running over: <b>{st?.enabled ? "HTTPS" : "HTTP"}</b>
        </p>
        <Field label="Certificate (PEM)"><input type="file" aria-label="Certificate file" accept=".pem,.crt,.cer" className="input"
          onChange={(e) => setCert(e.target.files?.[0] ?? null)} /></Field>
        <Field label="Private key (PEM)"><input type="file" aria-label="Private key file" accept=".pem,.key" className="input"
          onChange={(e) => setKey(e.target.files?.[0] ?? null)} /></Field>
        <Field label="CA certificate (optional)" hint="Shown for download below, so a phone can trust the certificate">
          <input type="file" aria-label="CA certificate file" accept=".pem,.crt,.cer" className="input"
            onChange={(e) => setCa(e.target.files?.[0] ?? null)} /></Field>
        <div className="flex flex-wrap items-center gap-3">
          <button className="btn-outline" disabled={busy || !cert || !key} onClick={upload}>
            {busy ? <Spinner /> : <Upload size={15} />} Upload certificate</button>
          {st?.has_ca && <a className="btn-ghost" href="/api/tls/ca" download>Download CA certificate</a>}
        </div>
        <div className="flex items-center justify-between gap-4 rounded-xl border border-line px-4 py-3">
          <span className="text-sm text-ink-700">Serve the dashboard over HTTPS</span>
          <Switch label="Serve over HTTPS" checked={!!st?.enabled} disabled={!st?.has_certificate || st?.pending}
            onChange={(v) => switchTo(v)} />
        </div>
      </div>
    </Section>
  );
}

function AccessSection() {
  const toast = useToast();
  const [acc, setAcc] = useState<AccessStatus | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { Api.access().then(setAcc).catch(() => {}); }, []);
  const change = async (on: boolean) => {
    setBusy(true);
    try {
      const a = await Api.setAccess(on); setAcc(a);
      toast("ok", on ? "The dashboard is reachable over the VPN side again"
                     : "The dashboard is blocked on the VPN side. LAN access and forwards are unaffected");
    } catch (e) { toast("error", e instanceof Error ? e.message : "Could not change the setting"); }
    finally { setBusy(false); }
  };
  return (
    <Section icon={<Server size={18} />} title="Dashboard access" text="Whether a device on the VPN side (Tailscale, on a Pi) can open this dashboard. Login still applies either way. LAN access and the port forwards are unaffected.">
      <div className="flex items-center justify-between gap-4 rounded-xl border border-line px-4 py-3">
        <span className="text-sm text-ink-700">
          Allow the dashboard over <b>{acc?.vpn_interface ?? "the VPN"}</b> (port {acc?.port ?? 8088})
        </span>
        <Switch label="Allow dashboard over VPN" checked={!!acc?.ui_over_vpn} disabled={!acc || busy}
          onChange={(v) => change(v)} />
      </div>
    </Section>
  );
}

export function Settings({ system, mustChange, onPasswordChanged, onImported }: {
  system: SystemInfo | null; mustChange: boolean; onPasswordChanged: () => void; onImported: () => void;
}) {
  const toast = useToast();
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [next2, setNext2] = useState("");
  const [pwBusy, setPwBusy] = useState(false);
  const [tokens, setTokens] = useState<TokenRow[]>([]);
  const [tokName, setTokName] = useState("");
  const [newTok, setNewTok] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [importData, setImportData] = useState<ForwardBody[] | null>(null);
  const [importMode, setImportMode] = useState<"merge" | "replace">("merge");
  const [appearance, setAppearance] = useState<"light" | "dark" | "system">(() => storedTheme() ?? "system");

  const loadTokens = () => Api.tokens().then(setTokens).catch(() => {});
  useEffect(() => { loadTokens(); }, []);

  // stays in sync if the theme was last changed from the header toggle, not this page
  useEffect(() => {
    const onThemeChange = () => setAppearance(storedTheme() ?? "system");
    window.addEventListener(THEME_CHANGE_EVENT, onThemeChange);
    return () => window.removeEventListener(THEME_CHANGE_EVENT, onThemeChange);
  }, []);

  const changePw = async (e: FormEvent) => {
    e.preventDefault();
    if (next !== next2) { toast("error", "The new passwords don't match"); return; }
    setPwBusy(true);
    try {
      await Api.changePassword(cur, next);
      toast("ok", "Password changed — sign in again");
      onPasswordChanged();
    } catch (err) {
      toast("error", (err as Error).message);
    } finally {
      setPwBusy(false);
    }
  };

  const createToken = async (e: FormEvent) => {
    e.preventDefault();
    if (!tokName.trim()) return;
    const t = await Api.createToken(tokName.trim());
    setNewTok(t.token);
    setTokName("");
    loadTokens();
  };

  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 1500); }
    catch { toast("info", "Copy blocked by the browser — select the token and copy it manually"); }
  };

  const doExport = async () => {
    const data = await Api.exportAll();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `portmirror-forwards-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast("ok", `Exported ${data.forwards.length} forwards`);
  };

  const pickImport = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      const items = Array.isArray(data) ? data : data.forwards;
      if (!Array.isArray(items)) throw new Error("no forwards array in file");
      setImportData(items);
    } catch (err) {
      toast("error", `Can't read that file: ${(err as Error).message}`);
    }
  };

  const doImport = async () => {
    if (!importData) return;
    try {
      const r = await Api.importAll(importData, importMode);
      toast("ok", `Imported ${r.imported} forwards (${importMode})`);
      setImportData(null);
      onImported();
    } catch (err) {
      toast("error", (err as Error).message);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-ink-950">Settings</h1>
        <p className="mt-1 text-sm text-ink-500">Access, automation and backups.</p>
      </div>

      <Section icon={<KeyRound size={18} />} title="Admin password" text="Signs you in to this console. Changing it signs out every session.">
        {mustChange && (
          <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm text-amber-800">
            You're still using the generated initial password. Set your own now.
          </div>
        )}
        <form onSubmit={changePw} className="grid max-w-md gap-4">
          <Field label="Current password"><input type="password" className="input" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" /></Field>
          <Field label="New password" hint="At least 10 characters"><input type="password" className="input" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" /></Field>
          <Field label="Repeat new password"><input type="password" className="input" value={next2} onChange={(e) => setNext2(e.target.value)} autoComplete="new-password" /></Field>
          <div><button className="btn-primary" disabled={pwBusy || next.length < 10 || !cur}>{pwBusy ? <Spinner /> : "Change password"}</button></div>
        </form>
      </Section>

      <Section icon={<KeyRound size={18} />} title="API tokens" text="For scripts and automation: send as “Authorization: Bearer …”. Tokens are shown once.">
        <form onSubmit={createToken} className="flex max-w-md gap-2">
          <input className="input" placeholder="Token name, e.g. ci-deploy" value={tokName} onChange={(e) => setTokName(e.target.value)} maxLength={64} />
          <button className="btn-primary shrink-0" disabled={!tokName.trim()}>Create</button>
        </form>
        <ul className="mt-4 divide-y divide-line rounded-xl border border-line">
          {tokens.length === 0 && <li className="px-4 py-3 text-sm text-ink-400">No tokens yet.</li>}
          {tokens.map((t) => (
            <li key={t.id} className="flex items-center gap-3 px-4 py-2.5">
              <span className="font-medium text-ink-900">{t.name}</span>
              <span className="text-xs text-ink-400">created {datetime(t.created_at)} · last used {ago(t.last_used)}</span>
              <button className="btn-ghost ml-auto h-8 w-8 px-0 hover:text-rose-600" aria-label={`Revoke ${t.name}`}
                onClick={async () => { await Api.deleteToken(t.id); loadTokens(); toast("ok", `Revoked “${t.name}”`); }}><Trash2 size={15} /></button>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-ink-400">Interactive API reference: <a className="text-accent-600 hover:underline" href="/api/docs" target="_blank" rel="noreferrer">/api/docs</a></p>
      </Section>

      <AccessSection />
      <HttpsSection />
      <Section icon={<Download size={18} />} title="Backup & restore" text="Export every forward as JSON; import to merge into, or replace, the current set.">
        <div className="flex flex-wrap gap-2">
          <button className="btn-outline" onClick={doExport}><Download size={15} /> Export JSON</button>
          <label className="btn-outline cursor-pointer"><Upload size={15} /> Import JSON…
            <input type="file" accept="application/json,.json" className="hidden" onChange={pickImport} />
          </label>
        </div>
      </Section>

      <Section icon={<Palette size={18} />} title="Appearance" text="Light, dark, or follow this device's own setting.">
        <Segmented value={appearance} onChange={(v: "light" | "dark" | "system") => { setAppearance(v); setTheme(v === "system" ? null : v); }}
          options={[{ value: "light", label: "Light" }, { value: "dark", label: "Dark" }, { value: "system", label: "System" }]} />
      </Section>

      <Section icon={<Server size={18} />} title="Gateway" text="Read-only view of how this gateway is wired.">
        {system ? (
          <dl className="grid gap-x-8 gap-y-3 font-mono text-[12.5px] sm:grid-cols-2">
            {[
              ["LAN interface", `${system.lan_if} · ${system.gw_lan_ip}`], ["VPN interface", `${system.vpn_if} · ${system.gw_vpn_ip}`],
              ["LAN network", system.lan_net], ["VPN network", system.vpn_net],
              ["Reserved TCP ports", system.reserved_tcp_ports.join(", ")], ["Max range size", `${system.max_range} ports`],
              ["Last kernel apply", system.last_apply ? datetime(system.last_apply) : "—"], ["Self-heal", system.hold ? "paused (hold file)" : "active"],
              ["History retention", system.history_retention_days ? `${system.history_retention_days} days` : "forever"],
              ["Audit retention", system.audit_retention_days ? `${system.audit_retention_days} days` : "forever"],
              ["Connection log retention", system.connection_log_retention_days ? `${system.connection_log_retention_days} days` : "forever"],
              ["History kept since", system.storage.oldest_rollup ? datetime(new Date(system.storage.oldest_rollup * 1000).toISOString()) : "—"],
              ["Database size", `${bytes(system.db_bytes)} · ${system.storage.rollups.toLocaleString()} history rows · ${system.storage.audit.toLocaleString()} audit entries · ${system.storage.connections.toLocaleString()} connections logged`],
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between gap-4 border-b border-line/70 pb-2">
                <dt className="font-sans text-ink-400">{k}</dt><dd className="text-right text-ink-900">{v}</dd>
              </div>
            ))}
          </dl>
        ) : <Spinner />}
      </Section>

      {newTok && (
        <Modal title="Your new API token" onClose={() => setNewTok(null)}
          footer={<button className="btn-primary" onClick={() => setNewTok(null)}>Done</button>}>
          <p className="mb-3">Copy it now — it won't be shown again.</p>
          <div className="flex items-center gap-2 rounded-lg border border-line bg-canvas p-2">
            <code className="min-w-0 flex-1 break-all font-mono text-xs text-ink-900">{newTok}</code>
            <button className="btn-outline h-8 shrink-0 px-2" onClick={() => copy(newTok)} aria-label="Copy token">
              {copied ? <Check size={14} className="text-emerald-600" /> : <Copy size={14} />}
            </button>
          </div>
        </Modal>
      )}

      {importData && (
        <Modal title={`Import ${importData.length} forwards`} onClose={() => setImportData(null)}
          footer={<>
            <button className="btn-outline" onClick={() => setImportData(null)}>Cancel</button>
            <button className={importMode === "replace" ? "btn-danger" : "btn-primary"} onClick={doImport}>
              {importMode === "replace" ? "Replace all forwards" : "Merge"}
            </button>
          </>}>
          <Segmented value={importMode} onChange={setImportMode}
            options={[{ value: "merge", label: "Merge" }, { value: "replace", label: "Replace everything" }]} />
          <p className="mt-3 text-xs text-ink-500">
            {importMode === "merge" ? "Adds these forwards next to the existing ones. Port conflicts abort the whole import."
              : "Deletes every current forward and creates these instead, in one atomic change."}
          </p>
          <ul className="mt-3 max-h-48 overflow-auto rounded-lg border border-line font-mono text-xs scroll-thin">
            {importData.slice(0, 50).map((f, i) => (
              <li key={i} className="flex justify-between gap-2 border-b border-line/70 px-3 py-1.5 last:border-0">
                <span className="truncate text-ink-900">{f.name}</span>
                <span className="shrink-0 text-ink-500">{f.protocol} :{f.listen_port} → {f.target_ip}:{f.target_port}</span>
              </li>
            ))}
          </ul>
        </Modal>
      )}
    </div>
  );
}
