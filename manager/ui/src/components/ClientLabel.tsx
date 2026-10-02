import { Pencil } from "lucide-react";
import { KeyboardEvent, useState } from "react";
import { Api } from "../lib/api";
import { useToast } from "./Toasts";
import { cx } from "./ui";

/** A client IP, shown with its nickname when one's set ("Dad's laptop (192.168.88.67)") and
 * editable inline - click it, type a name, Enter to save (or clear it and save to remove the
 * name). Purely cosmetic: the raw IP is always what's actually used underneath. */
export function ClientLabel({ ip, deviceNames, onChanged, className }: {
  ip: string; deviceNames: Record<string, string>; onChanged: () => void; className?: string;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const name = deviceNames[ip];

  const start = () => {
    setDraft(name ?? "");
    setEditing(true);
  };

  const save = async () => {
    const v = draft.trim();
    if (v === (name ?? "")) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      if (v) await Api.setDeviceName(ip, v);
      else await Api.deleteDeviceName(ip);
      onChanged();
      setEditing(false);
    } catch (e) {
      toast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") (e.target as HTMLInputElement).blur();
    if (e.key === "Escape") setEditing(false);
  };

  if (editing) {
    return (
      <input autoFocus className={cx("input h-6 w-36 px-1.5 py-0 text-xs", className)} placeholder={ip}
        value={draft} disabled={saving} onClick={(e) => e.stopPropagation()}
        onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} onBlur={save} />
    );
  }

  return (
    <button type="button" onClick={(e) => { e.stopPropagation(); start(); }}
      className={cx("group inline-flex max-w-full items-center gap-1.5 text-left", className)}
      title={name ? `${name} (${ip}) — click to rename` : `${ip} — click to name this device`}>
      {name ? <><span className="truncate">{name}</span><span className="shrink-0 text-ink-400">({ip})</span></>
        : <span className="truncate">{ip}</span>}
      <Pencil size={11} className="shrink-0 text-ink-400 opacity-0 group-hover:opacity-100" />
    </button>
  );
}
