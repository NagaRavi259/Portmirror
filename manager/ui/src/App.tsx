import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ForwardForm } from "./components/ForwardForm";
import { RemoveDialog } from "./components/RemoveDialog";
import { Shell } from "./components/Shell";
import { useToast } from "./components/Toasts";
import { Spinner } from "./components/ui";
import { Api, Forward, SystemInfo, setUnauthorizedHandler } from "./lib/api";
import { useLive } from "./lib/live";
import { go, useRoute } from "./lib/router";
import { Audit } from "./pages/Audit";
import { Dashboard } from "./pages/Dashboard";
import { ForwardDetail } from "./pages/ForwardDetail";
import { Login } from "./pages/Login";
import { Settings } from "./pages/Settings";

type Auth = { state: "checking" } | { state: "anon" } | { state: "in"; user: string; mustChange: boolean };

export default function App() {
  const toast = useToast();
  const route = useRoute();
  const [auth, setAuth] = useState<Auth>({ state: "checking" });
  const [forwards, setForwards] = useState<Forward[]>([]);
  const [loading, setLoading] = useState(true);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [editing, setEditing] = useState<{ forward?: Forward } | null>(null);
  const [removing, setRemoving] = useState<{ forward: Forward; mode: "delete" | "disable" } | null>(null);
  const { snap, link } = useLive(auth.state === "in");
  const version = useRef<number | null>(null);

  useEffect(() => {
    setUnauthorizedHandler(() => setAuth({ state: "anon" }));
    Api.me().then((m) => setAuth({ state: "in", user: m.username, mustChange: m.must_change_password }))
      .catch(() => setAuth({ state: "anon" }));
  }, []);

  const load = useCallback(async () => {
    try {
      setForwards(await Api.forwards());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (auth.state !== "in") return;
    load();
    Api.system().then(setSystem).catch(() => {});
  }, [auth.state, load]);

  // refetch definitions whenever the server says forwards changed (any client, expiry, import...)
  useEffect(() => {
    const v = snap?.global.version;
    if (v === undefined) return;
    if (version.current !== null && v !== version.current) load();
    version.current = v;
  }, [snap?.global.version, load]);

  // merge live stats + sparklines into the definitions
  const live = useMemo(() => forwards.map((f) => {
    const st = snap?.forwards[String(f.id)];
    return st ? { ...f, stats: st, health: st.health ?? f.health, spark: snap?.spark?.[String(f.id)] ?? f.spark } : f;
  }), [forwards, snap]);

  const onToggle = async (f: Forward, on: boolean) => {
    if (!on && (f.stats?.live ?? 0) > 0) { setRemoving({ forward: f, mode: "disable" }); return; }
    try {
      await Api.toggle(f.id, on);
      toast("ok", `${on ? "Enabled" : "Disabled"} “${f.name}”`);
      load();
    } catch (e) {
      toast("error", (e as Error).message);
    }
  };

  const confirmRemove = async (kill: boolean) => {
    if (!removing) return;
    const { forward: f, mode } = removing;
    try {
      if (mode === "delete") {
        const r = await Api.remove(f.id, kill);
        toast("ok", `Deleted “${f.name}”${r.killed ? ` · cut ${r.killed} connection${r.killed === 1 ? "" : "s"}` : ""}`);
        if (route.page === "forward") go("/");
      } else {
        await Api.toggle(f.id, false, kill);
        toast("ok", `Disabled “${f.name}”${kill ? " and cut its connections" : " · live connections drain"}`);
      }
      setRemoving(null);
      load();
    } catch (e) {
      toast("error", (e as Error).message);
    }
  };

  if (auth.state === "checking") {
    return <div className="grid min-h-screen place-items-center text-accent-500"><Spinner className="h-6 w-6" /></div>;
  }
  if (auth.state === "anon") {
    return <Login onDone={(user, mustChange) => { setAuth({ state: "in", user, mustChange }); if (mustChange) go("settings"); }} />;
  }

  const current = route.page === "forward" ? live.find((f) => f.id === route.id) : undefined;

  return (
    <Shell route={route} link={link} global={snap?.global} system={system} user={auth.user}
      onLogout={async () => { await Api.logout().catch(() => {}); setAuth({ state: "anon" }); }}>
      {auth.mustChange && route.page !== "settings" && (
        <a href="#/settings" className="mb-6 flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 hover:bg-amber-100/70">
          <b>Security:</b> you're using the generated initial password — set your own in Settings →
        </a>
      )}
      {route.page === "dashboard" && (
        <Dashboard forwards={live} snap={snap} loading={loading} onNew={() => setEditing({})}
          onEdit={(f) => setEditing({ forward: f })} onRemove={(f) => setRemoving({ forward: f, mode: "delete" })} onToggle={onToggle} />
      )}
      {route.page === "forward" && (
        <ForwardDetail forward={current} snap={snap} system={system} onEdit={(f) => setEditing({ forward: f })}
          onRemove={(f) => setRemoving({ forward: f, mode: "delete" })} onToggle={onToggle} />
      )}
      {route.page === "audit" && <Audit />}
      {route.page === "settings" && (
        <Settings system={system} mustChange={auth.mustChange} onImported={load}
          onPasswordChanged={() => setAuth({ state: "anon" })} />
      )}

      {editing && (
        <ForwardForm forward={editing.forward ? live.find((f) => f.id === editing.forward!.id) ?? editing.forward : undefined}
          all={live} system={system} onClose={() => setEditing(null)}
          onSaved={(f) => { setEditing(null); load(); if (!editing.forward) go(`forwards/${f.id}`); }} />
      )}
      {removing && (
        <RemoveDialog forward={live.find((f) => f.id === removing.forward.id) ?? removing.forward} mode={removing.mode}
          onCancel={() => setRemoving(null)} onConfirm={confirmRemove} />
      )}
    </Shell>
  );
}
