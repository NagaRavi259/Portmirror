import { ExternalLink, X } from "lucide-react";
import { ReactNode, useEffect } from "react";
import { createPortal } from "react-dom";
import { forwardUrl, ports } from "../lib/format";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cx("card", className)}>{children}</div>;
}

export function StatusDot({ state, pulse }: { state: "up" | "down" | "off" | "unknown" | "warn" | "quota"; pulse?: boolean }) {
  const color = { up: "bg-emerald-500", down: "bg-rose-500", off: "bg-ink-300", unknown: "bg-ink-300", warn: "bg-amber-500", quota: "bg-violet-500" }[state];
  const ring = { up: "bg-emerald-400/40", down: "bg-rose-400/40", off: "", unknown: "", warn: "bg-amber-400/40", quota: "bg-violet-400/40" }[state];
  return (
    <span className="relative inline-flex h-2.5 w-2.5 shrink-0">
      {pulse && ring && <span className={cx("absolute inset-0 rounded-full animate-pulse2", ring)} style={{ transform: "scale(1.9)" }} />}
      <span className={cx("relative inline-flex h-2.5 w-2.5 rounded-full ring-2 ring-white", color)} />
    </span>
  );
}

export function ProtoBadge({ protocol }: { protocol: string }) {
  const style =
    protocol === "tcp"
      ? "bg-accent-50 text-accent-600 ring-accent-100"
      : protocol === "udp"
        ? "bg-signal-50 text-signal-600 ring-cyan-100"
        : "bg-gradient-to-r from-accent-50 to-signal-50 text-accent-600 ring-accent-100";
  return (
    <span className={cx("chip ring-1 font-mono tracking-wide", style)}>
      {protocol === "both" ? "TCP+UDP" : protocol.toUpperCase()}
    </span>
  );
}

export function Switch({ checked, onChange, disabled, label }: {
  checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: string;
}) {
  return (
    <button
      type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
      onClick={(e) => { e.stopPropagation(); onChange(!checked); }}
      className={cx(
        "relative inline-flex h-[22px] w-10 shrink-0 items-center rounded-full transition-colors duration-200 disabled:opacity-50",
        checked ? "bg-gradient-to-r from-accent-500 to-signal-500" : "bg-ink-300/60",
      )}
    >
      <span className={cx("inline-block h-[18px] w-[18px] rounded-full bg-white shadow transition-transform duration-200",
        checked ? "translate-x-[20px]" : "translate-x-[2px]")} />
    </button>
  );
}

export function Segmented<T extends string>({ value, options, onChange }: {
  value: T; options: { value: T; label: string }[]; onChange: (v: T) => void;
}) {
  return (
    <div className="inline-flex rounded-lg bg-ink-950/[.045] p-0.5">
      {options.map((o) => (
        <button
          key={o.value} type="button" onClick={() => onChange(o.value)}
          className={cx("h-8 px-3 rounded-md text-xs font-semibold transition",
            value === o.value ? "bg-white text-ink-900 shadow-sm" : "text-ink-500 hover:text-ink-900")}
        >{o.label}</button>
      ))}
    </div>
  );
}

export function Field({ label, hint, error, children, className }: {
  label: string; hint?: ReactNode; error?: string; children: ReactNode; className?: string;
}) {
  return (
    <label className={cx("block", className)}>
      <span className="mb-1.5 block text-[13px] font-medium text-ink-700">{label}</span>
      {children}
      {error ? <span className="mt-1 block text-xs text-rose-600">{error}</span>
        : hint ? <span className="mt-1 block text-xs text-ink-400">{hint}</span> : null}
    </label>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <span className={cx("inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent", className)} />;
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center py-14 text-center">
      <div className="mb-3 grid h-12 w-12 place-items-center rounded-2xl bg-gradient-to-br from-accent-50 to-signal-50 text-accent-500">{icon}</div>
      <p className="font-semibold text-ink-900">{title}</p>
      {children && <div className="mt-1 max-w-sm text-sm text-ink-500">{children}</div>}
    </div>
  );
}

function useEscape(onClose: () => void) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [onClose]);
}

export function Modal({ title, onClose, children, footer, icon }: {
  title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; icon?: ReactNode;
}) {
  useEscape(onClose);
  return createPortal(
    <div className="fixed inset-0 z-50 grid place-items-center p-4">
      {/* always-black scrim, deliberately not the theme-flipping ink-950 - a backdrop must dim in both themes */}
      <div className="absolute inset-0 bg-black/25 backdrop-blur-[2px]" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label={title} className="relative w-full max-w-md rounded-2xl border border-line bg-white shadow-lift animate-fadeup">
        <div className="flex items-start gap-3 p-5 pb-3">
          {icon}
          <h2 className="flex-1 pt-0.5 text-base font-semibold text-ink-950">{title}</h2>
          <button className="btn-ghost -mr-2 -mt-1 h-8 w-8 px-0" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>
        <div className="px-5 pb-5 text-sm text-ink-700">{children}</div>
        {footer && <div className="flex justify-end gap-2 rounded-b-2xl border-t border-line bg-canvas/70 px-5 py-3">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function Drawer({ title, subtitle, onClose, children, footer }: {
  title: string; subtitle?: string; onClose: () => void; children: ReactNode; footer: ReactNode;
}) {
  useEscape(onClose);
  return createPortal(
    <div className="fixed inset-0 z-40">
      <div className="absolute inset-0 bg-black/20 backdrop-blur-[2px]" onClick={onClose} />
      <aside role="dialog" aria-modal="true" aria-label={title}
        className="absolute right-0 top-0 flex h-full w-full max-w-[520px] flex-col border-l border-line bg-white shadow-lift animate-slidein">
        <div className="flex items-start gap-3 border-b border-line px-6 py-5">
          <div className="flex-1">
            <h2 className="text-lg font-semibold text-ink-950">{title}</h2>
            {subtitle && <p className="mt-0.5 text-sm text-ink-500">{subtitle}</p>}
          </div>
          <button className="btn-ghost -mr-2 h-8 w-8 px-0" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </div>
        <div className="flex-1 overflow-y-auto scroll-thin px-6 py-5">{children}</div>
        <div className="flex justify-end gap-2 border-t border-line bg-canvas/70 px-6 py-4">{footer}</div>
      </aside>
    </div>,
    document.body,
  );
}


/** The local port, as a link that opens the forward in a new tab when there's somewhere
 * meaningful to send the browser (see `forwardUrl`) - otherwise, just the plain port text. */
export function PortLink({ forward, gwLanIp, className }: {
  forward: { protocol: string; enabled: boolean; expired: boolean; listen_port: number; listen_port_end: number | null };
  gwLanIp: string | undefined | null; className?: string;
}) {
  const label = `:${ports(forward.listen_port, forward.listen_port_end)}`;
  const url = forwardUrl(forward, gwLanIp);
  if (!url) return <span className={className}>{label}</span>;
  return (
    <a href={url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
      className={cx(className, "inline-flex items-center gap-1")} title={`Open ${url} in a new tab`}>
      {label}<ExternalLink size={11} className="opacity-60" />
    </a>
  );
}
