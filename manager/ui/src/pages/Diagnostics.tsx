import { AlertTriangle, CheckCircle2, RefreshCw, Stethoscope, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useToast } from "../components/Toasts";
import { Card, Spinner, StatusDot, cx } from "../components/ui";
import { Api, DiagCheck } from "../lib/api";
import { datetime } from "../lib/format";

const DOT: Record<DiagCheck["status"], "up" | "warn" | "down" | "off"> = {
  ok: "up", warn: "warn", fail: "down", skip: "off",
};
const ICON: Record<DiagCheck["status"], typeof CheckCircle2> = {
  ok: CheckCircle2, warn: AlertTriangle, fail: XCircle, skip: AlertTriangle,
};
const TEXT: Record<DiagCheck["status"], string> = {
  ok: "text-emerald-600", warn: "text-amber-600", fail: "text-rose-600", skip: "text-ink-400",
};

export function Diagnostics() {
  const toast = useToast();
  const [checks, setChecks] = useState<DiagCheck[] | null>(null);
  const [ok, setOk] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [ranAt, setRanAt] = useState<string | null>(null);

  const run = useCallback(async () => {
    setLoading(true);
    try {
      const r = await Api.diag();
      setChecks(r.checks);
      setOk(r.ok);
      setRanAt(new Date().toISOString());
    } catch (e) {
      toast("error", (e as Error).message);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { run(); }, [run]);

  const failing = checks?.filter((c) => c.status === "fail").length ?? 0;
  const warning = checks?.filter((c) => c.status === "warn").length ?? 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink-950">Diagnostics</h1>
          <p className="mt-1 text-sm text-ink-500">
            Everything worth checking by hand, in one place: ruleset validity, routing, interfaces,
            resource headroom, and target health.
          </p>
        </div>
        <button className="btn-outline" disabled={loading} onClick={run}>
          {loading ? <Spinner /> : <RefreshCw size={15} />} Run again
        </button>
      </div>

      {checks && (
        <div className={cx("flex items-center gap-3 rounded-xl border px-4 py-3 text-sm",
          ok ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-rose-200 bg-rose-50 text-rose-800")}>
          <StatusDot state={ok ? "up" : "down"} />
          {ok ? "Everything checks out." : `${failing} check${failing === 1 ? "" : "s"} failing${warning ? `, ${warning} warning${warning === 1 ? "" : "s"}` : ""}.`}
          {ranAt && <span className="ml-auto text-xs text-ink-400">as of {datetime(ranAt)}</span>}
        </div>
      )}

      <Card className="overflow-hidden">
        {!checks ? (
          <div className="grid h-40 place-items-center"><Spinner className="h-5 w-5 text-accent-500" /></div>
        ) : (
          <ul className="divide-y divide-line">
            {checks.map((c) => {
              const Icon = ICON[c.status];
              return (
                <li key={c.id} className="flex items-center gap-4 px-5 py-3.5">
                  <Icon size={18} className={cx("shrink-0", TEXT[c.status])} />
                  <span className="w-56 shrink-0 text-sm font-medium text-ink-900">{c.label}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-500">{c.detail}</span>
                  <StatusDot state={DOT[c.status]} />
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      {checks && (
        <p className="flex items-center gap-1.5 text-xs text-ink-400">
          <Stethoscope size={13} /> The same checks are available from a shell with <code className="font-mono">pmctl diag</code>.
        </p>
      )}
    </div>
  );
}
