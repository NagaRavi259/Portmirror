import { Check, Copy, Download, KeyRound, Server, Trash2, Upload } from "lucide-react";
import { ChangeEvent, FormEvent, ReactNode, useEffect, useState } from "react";
import { useToast } from "../components/Toasts";
import { Card, Field, Modal, Segmented, Spinner } from "../components/ui";
import { Api, ForwardBody, SystemInfo, TokenRow } from "../lib/api";
import { ago, bytes, datetime } from "../lib/format";

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

  const loadTokens = () => Api.tokens().then(setTokens).catch(() => {});
  useEffect(() => { loadTokens(); }, []);

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

      <Section icon={<Download size={18} />} title="Backup & restore" text="Export every forward as JSON; import to merge into, or replace, the current set.">
        <div className="flex flex-wrap gap-2">
          <button className="btn-outline" onClick={doExport}><Download size={15} /> Export JSON</button>
          <label className="btn-outline cursor-pointer"><Upload size={15} /> Import JSON…
            <input type="file" accept="application/json,.json" className="hidden" onChange={pickImport} />
          </label>
        </div>
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
              ["History kept since", system.storage.oldest_rollup ? datetime(new Date(system.storage.oldest_rollup * 1000).toISOString()) : "—"],
              ["Database size", `${bytes(system.db_bytes)} · ${system.storage.rollups.toLocaleString()} history rows · ${system.storage.audit.toLocaleString()} audit entries`],
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
