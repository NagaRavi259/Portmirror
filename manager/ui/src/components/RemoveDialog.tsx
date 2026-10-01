import { PowerOff, Trash2, Unplug, Waves } from "lucide-react";
import { ReactNode, useState } from "react";
import { Forward } from "../lib/api";
import { ports, protoLabel } from "../lib/format";
import { Modal, Spinner, cx } from "./ui";

/** Delete or disable a forward, asking what happens to live connections. */
export function RemoveDialog({ forward, mode, onCancel, onConfirm }: {
  forward: Forward; mode: "delete" | "disable"; onCancel: () => void; onConfirm: (kill: boolean) => Promise<void>;
}) {
  const live = forward.stats?.live ?? 0;
  const [kill, setKill] = useState(false);
  const [busy, setBusy] = useState(false);
  const verb = mode === "delete" ? "Delete" : "Disable";

  const go = async () => {
    setBusy(true);
    try { await onConfirm(kill); } finally { setBusy(false); }
  };

  const Option = ({ value, icon, title, text }: { value: boolean; icon: ReactNode; title: string; text: string }) => (
    <button type="button" onClick={() => setKill(value)}
      className={cx("flex w-full items-start gap-3 rounded-xl border p-3 text-left transition",
        kill === value ? "border-accent-400 bg-accent-50/60 shadow-glow" : "border-line hover:border-line-strong")}>
      <span className={cx("mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg",
        kill === value ? "bg-white text-accent-500 shadow-sm" : "bg-canvas text-ink-400")}>{icon}</span>
      <span>
        <span className="block text-sm font-semibold text-ink-900">{title}</span>
        <span className="block text-xs text-ink-500">{text}</span>
      </span>
    </button>
  );

  return (
    <Modal
      title={`${verb} “${forward.name}”?`}
      onClose={onCancel}
      icon={<span className={cx("grid h-9 w-9 place-items-center rounded-xl",
        mode === "delete" ? "bg-rose-50 text-rose-500" : "bg-amber-50 text-amber-600")}>
        {mode === "delete" ? <Trash2 size={18} /> : <PowerOff size={18} />}</span>}
      footer={<>
        <button className="btn-outline" onClick={onCancel}>Cancel</button>
        <button className={mode === "delete" ? "btn-danger" : "btn-primary"} onClick={go} disabled={busy}>
          {busy ? <Spinner /> : kill && live > 0 ? `${verb} & cut ${live}` : verb}
        </button>
      </>}
    >
      <p>
        <span className="font-mono text-ink-900">:{ports(forward.listen_port, forward.listen_port_end)}</span> ({protoLabel(forward.protocol)}) stops accepting new connections immediately.
        {mode === "delete" && " Its settings and traffic totals are removed."}
      </p>
      {live > 0 ? (
        <div className="mt-4 space-y-2">
          <p className="label">{live} live connection{live === 1 ? "" : "s"} right now</p>
          <Option value={false} icon={<Waves size={16} />} title="Let them drain"
            text="Existing sessions continue until they close on their own." />
          <Option value={true} icon={<Unplug size={16} />} title="Cut them now"
            text="TCP sessions get a reset; UDP flows stop. Clients see a disconnect." />
        </div>
      ) : (
        <p className="mt-3 text-xs text-ink-500">No live connections — nothing will be interrupted.</p>
      )}
    </Modal>
  );
}
