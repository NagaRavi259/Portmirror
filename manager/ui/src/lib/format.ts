export function bps(v: number | undefined | null): string {
  const n = v ?? 0;
  if (n < 1000) return `${Math.round(n)} bps`;
  const units = ["Kbps", "Mbps", "Gbps", "Tbps"];
  let x = n / 1000;
  let i = 0;
  while (x >= 1000 && i < units.length - 1) {
    x /= 1000;
    i++;
  }
  return `${x < 10 ? x.toFixed(2) : x < 100 ? x.toFixed(1) : Math.round(x)} ${units[i]}`;
}

export function bytes(v: number | undefined | null): string {
  const n = v ?? 0;
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let x = n / 1024;
  let i = 0;
  while (x >= 1024 && i < units.length - 1) {
    x /= 1024;
    i++;
  }
  return `${x < 10 ? x.toFixed(2) : x < 100 ? x.toFixed(1) : Math.round(x)} ${units[i]}`;
}

export function count(v: number | undefined | null): string {
  const n = v ?? 0;
  if (n < 10_000) return n.toLocaleString("en-US");
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 100_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

export function rate(v: number | undefined | null): string {
  const n = v ?? 0;
  return n === 0 ? "0" : n < 10 ? n.toFixed(1) : Math.round(n).toLocaleString("en-US");
}

export function duration(s: number): string {
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

export function ago(iso: string | number | null | undefined): string {
  if (!iso) return "never";
  const t = typeof iso === "number" ? iso * 1000 : Date.parse(iso);
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 5) return "just now";
  return `${duration(s)} ago`;
}

export function until(iso: string | null): string {
  if (!iso) return "";
  const s = (Date.parse(iso) - Date.now()) / 1000;
  return s <= 0 ? "expired" : `in ${duration(s)}`;
}

export function ports(start: number, end: number | null | undefined): string {
  return end ? `${start}–${end}` : `${start}`;
}

export function protoLabel(p: string): string {
  return p === "both" ? "TCP+UDP" : p.toUpperCase();
}

/** A forward is only worth opening as a link if it's enabled, not expired, speaks TCP, and
 * there's somewhere to send the browser - a UDP-only forward or an unknown gateway address
 * can't offer a meaningful one. */
export function forwardUrl(f: { protocol: string; enabled: boolean; expired: boolean; listen_port: number },
                           gwLanIp: string | undefined | null): string | null {
  if (!gwLanIp || f.protocol === "udp" || !f.enabled || f.expired) return null;
  return `http://${gwLanIp}:${f.listen_port}/`;
}

export function datetime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Annotates any IPv4 address found in free-form text with its nickname, if one's set - e.g. the
 * audit log, which mentions client addresses inside arbitrary strings rather than a discrete field,
 * so this is a text-level pass rather than a dedicated component like ClientLabel. */
export function withDeviceNames(text: string | null | undefined, deviceNames: Record<string, string>): string {
  if (!text || Object.keys(deviceNames).length === 0) return text ?? "";
  return text.replace(/\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/g, (ip) => {
    const name = deviceNames[ip];
    return name ? `${ip} (${name})` : ip;
  });
}
