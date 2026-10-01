import { ArrowRight, Lock } from "lucide-react";
import { FormEvent, useState } from "react";
import { Spinner } from "../components/ui";
import { Api } from "../lib/api";

export function Login({ onDone }: { onDone: (user: string, mustChange: boolean) => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await Api.login(username, password);
      onDone(r.username, r.must_change_password);
    } catch (ex) {
      setErr((ex as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative grid min-h-screen place-items-center overflow-hidden px-4">
      <div className="pointer-events-none absolute -top-40 left-1/2 h-[520px] w-[820px] -translate-x-1/2 rounded-full bg-gradient-to-r from-accent-400/20 via-signal-400/15 to-accent-400/10 blur-3xl" />
      <div className="relative w-full max-w-[400px] animate-fadeup">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="grid h-14 w-14 place-items-center rounded-2xl bg-gradient-to-br from-accent-500 to-signal-500 shadow-lift">
            <svg viewBox="0 0 32 32" className="h-8 w-8"><path d="M7 12h14l-3.5-3.5M25 20H11l3.5 3.5" stroke="#fff" strokeWidth="2.6" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </div>
          <h1 className="mt-5 text-2xl font-semibold tracking-tight text-ink-950">Portmirror</h1>
          <p className="mt-1 text-sm text-ink-500">Sign in to manage the gateway's port forwards</p>
        </div>
        <form onSubmit={submit} className="card space-y-4 p-6 shadow-lift">
          <label className="block">
            <span className="mb-1.5 block text-[13px] font-medium text-ink-700">Username</span>
            <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-[13px] font-medium text-ink-700">Password</span>
            <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password" autoFocus />
          </label>
          {err && <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{err}</p>}
          <button className="btn-primary h-10 w-full" disabled={busy || !password}>
            {busy ? <Spinner /> : <>Sign in <ArrowRight size={16} /></>}
          </button>
        </form>
        <p className="mt-6 flex items-center justify-center gap-1.5 text-xs text-ink-400">
          <Lock size={12} /> First sign-in: run <span className="kbd">docker exec pm-E pmctl password</span>
        </p>
      </div>
    </div>
  );
}
